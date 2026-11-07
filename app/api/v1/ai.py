from typing import Literal
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import current_user, get_db, require_editor
from app.core.config import settings
from app.core.errors import DomainError, envelope
from app.middleware.limits import consume_ai_daily_quota
from app.models import Article, User
from app.repositories.articles import visible_conditions
from app.services import ai_assistant

router=APIRouter()


class AITextInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    content: str | None=Field(default=None,max_length=30000)
    text: str | None=Field(default=None,max_length=30000)

    @model_validator(mode="after")
    def require_text(self):
        if not (self.content if self.content is not None else self.text or "").strip():
            raise ValueError("content is required")
        return self

    def value(self) -> str:
        return (self.content if self.content is not None else self.text or "").strip()


class AdminSummaryInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    article_id: int | None=Field(default=None,alias="articleId",gt=0)
    content: str | None=Field(default=None,max_length=30000)

    @model_validator(mode="after")
    def require_source(self):
        if self.article_id is None and not (self.content or "").strip():
            raise ValueError("articleId or content is required")
        return self


class ChatMessage(BaseModel):
    model_config=ConfigDict(extra="forbid")
    role: Literal["user","assistant"]
    content: str=Field(min_length=1,max_length=4000)

    @model_validator(mode="after")
    def require_nonblank(self):
        if not self.content.strip():
            raise ValueError("history content is required")
        return self


class AIAskInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    question: str=Field(min_length=1,max_length=4000)
    article_id: int | None=Field(default=None,alias="articleId",gt=0)
    history: list[ChatMessage]=Field(default_factory=list,max_length=10)

    @model_validator(mode="after")
    def require_nonblank_question(self):
        if not self.question.strip():
            raise ValueError("question is required")
        return self


async def consume_ai_quota(request: Request) -> None:
    if not settings.chat_base_url or not settings.chat_api_key:
        raise DomainError("AI 服务尚未配置",503)
    client_ip=request.client.host if request.client else "unknown"
    allowed=await consume_ai_daily_quota(getattr(request.app.state,"redis",None),client_ip)
    if allowed is False:
        raise DomainError("今日 AI 使用次数已达上限，请明天再试",429)


@router.post("/ai/summary")
async def summary(body: AITextInput, request: Request, user: User=Depends(current_user)):
    await consume_ai_quota(request)
    result=await ai_assistant.complete("Summarize the supplied article in concise Chinese.",body.value())
    return envelope({"summary":result,"content":result})


@router.post("/ai/polish")
async def polish(body: AITextInput, request: Request, user: User=Depends(current_user)):
    await consume_ai_quota(request)
    events=await ai_assistant.polish_events(body.value())
    return StreamingResponse(events,media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@router.post("/admin/ai/summary")
async def admin_summary(body: AdminSummaryInput, request: Request, db: Session=Depends(get_db), user: User=Depends(require_editor)):
    if body.article_id is not None:
        article=db.get(Article,body.article_id)
        if not article or article.deleted_at: raise DomainError("article not found",404)
        content=article.content
    else:
        content=body.content or ""
    await consume_ai_quota(request)
    result=await ai_assistant.complete("Summarize the supplied article in concise Chinese.",content)
    return envelope({"summary":result,"content":result})


@router.post("/admin/ai/polish")
async def admin_polish(body: AITextInput, request: Request, user: User=Depends(require_editor)):
    await consume_ai_quota(request)
    events=await ai_assistant.polish_events(body.value())
    return StreamingResponse(events,media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@router.post("/articles/{article_id}/summary")
async def summarize_article(article_id: int, request: Request, db: Session=Depends(get_db)):
    a=db.scalar(select(Article).where(Article.id==article_id,*visible_conditions(),Article.password==""))
    if not a: raise DomainError("article not found",404)
    await consume_ai_quota(request)
    return envelope({"summary":await ai_assistant.complete("Summarize the article in Chinese.",a.content[:30000])})


@router.post("/ai/ask")
async def ask(body: AIAskInput, request: Request, db: Session=Depends(get_db)):
    question=body.question.strip()
    article_id=body.article_id
    if article_id is not None:
        article=db.scalar(select(Article).where(Article.id==article_id,*visible_conditions(),Article.password==""))
        if not article: raise DomainError("article not found",404)
    if not settings.embedding_base_url or not settings.embedding_api_key:
        raise DomainError("Embedding 服务尚未配置",503)
    await consume_ai_quota(request)
    history=[{"role":item.role,"content":item.content} for item in body.history[-10:]]
    events=await ai_assistant.answer_events(db, question, article_id, history)
    return StreamingResponse(events,media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
