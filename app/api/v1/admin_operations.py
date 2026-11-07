from datetime import date, datetime, time, timedelta
from fastapi import APIRouter, Depends, File, UploadFile, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.api.deps import get_db, require_admin, require_editor
from app.core.config import settings
from app.core.errors import DomainError, envelope
from app.core.security import hash_password
from app.core.security import hash_password
from app.models import Article, AuditLog, Category, Comment, Image, Link, MetricEvent, SiteSetting, Tag, User
from app.api.v1.articles import article_data
from app.api.v1.articles import _parse_datetime
from app.services import images as image_service

router = APIRouter(prefix="/admin")


class QuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: StrictStr = Field(min_length=1, max_length=255)
    author: StrictStr = Field(default="佚名", max_length=100)


class SiteSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    site_title: StrictStr = Field(default="", max_length=255)
    site_subtitle: StrictStr = Field(default="", max_length=255)
    site_description: StrictStr = Field(default="", max_length=255)
    site_logo: StrictStr = Field(default="", max_length=255)
    site_beian: StrictStr = Field(default="", max_length=255)
    social_github: StrictStr = Field(default="", max_length=255)
    social_email: StrictStr = Field(default="", max_length=255)
    quotes: list[QuoteInput] = Field(default_factory=list, max_length=100)


class LinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: StrictStr = Field(min_length=1, max_length=50)
    url: StrictStr = Field(min_length=1, max_length=255)
    description: StrictStr = Field(default="", max_length=200)
    sort: StrictInt = Field(default=0, ge=-2147483648, le=2147483647)
    enabled: StrictBool = True

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()

    @field_validator("url")
    @classmethod
    def valid_http_url(cls, value: str) -> str:
        from urllib.parse import urlsplit
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or any(char.isspace() for char in parsed.hostname):
            raise ValueError("URL must be an absolute HTTP or HTTPS URL")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("URL has an invalid port") from exc
        return value.strip()

@router.get("/categories")
def admin_categories(db: Session = Depends(get_db), user: User = Depends(require_editor)):
    rows=db.scalars(select(Category).where(Category.deleted_at.is_(None)).order_by(Category.sort,Category.id)).all()
    counts = dict(db.execute(select(Article.category_id, func.count(Article.id)).where(
        Article.deleted_at.is_(None)
    ).group_by(Article.category_id)).all())
    return envelope([{"id":x.id,"name":x.name,"description":x.description,"sort":x.sort,
                      "articleCount":counts.get(x.id, 0)} for x in rows])

@router.get("/settings")
def settings_list(db: Session = Depends(get_db), user: User = Depends(require_editor)):
    editable={"site_title","site_subtitle","site_description","site_logo","site_beian","social_github","social_email"}
    values={x.key:x.value for x in db.scalars(select(SiteSetting)).all() if x.key in editable}
    raw_setting=db.get(SiteSetting,"daily_quotes")
    raw=raw_setting.value if raw_setting else ""
    values["quotes"]=[{"content":part.split("|",1)[0].strip(),"author":part.split("|",1)[1].strip() if "|" in part and part.split("|",1)[1].strip() else "佚名"} for part in raw.splitlines() if part.strip()]
    return envelope(values)

@router.put("/settings")
def update_settings(body: SiteSettingsInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    values={key:getattr(body,key) for key in ("site_title","site_subtitle","site_description","site_logo","site_beian","social_github","social_email") if key in body.model_fields_set}
    if "quotes" in body.model_fields_set:
        values["daily_quotes"]="\n".join(f"{q.content.strip()} | {q.author.strip() or '佚名'}" for q in body.quotes)
    for key, value in values.items():
        item = db.get(SiteSetting, str(key))
        if item: item.value = str(value)
        else: db.add(SiteSetting(key=str(key), value=str(value)))
    db.commit(); return envelope({"updated":True})

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(require_editor)):
    hot=db.scalars(select(Article).where(Article.status==1,Article.deleted_at.is_(None),Article.password=="").order_by(Article.view_count.desc()).limit(5)).all()
    recent=db.scalars(select(Comment).where(Comment.deleted_at.is_(None)).order_by(Comment.created_at.desc()).limit(5)).all()
    counts={"articles":db.scalar(select(func.count(Article.id)).where(Article.deleted_at.is_(None))) or 0,"users":db.scalar(select(func.count(User.id)).where(User.deleted_at.is_(None))) or 0,"views":db.scalar(select(func.coalesce(func.sum(Article.view_count),0)).where(Article.deleted_at.is_(None))) or 0,"comments":db.scalar(select(func.count(Comment.id)).where(Comment.deleted_at.is_(None))) or 0,"pendingComments":db.scalar(select(func.count(Comment.id)).where(Comment.status==0,Comment.deleted_at.is_(None))) or 0}
    return envelope({"counts":counts,"hotArticles":[article_data(a,False) for a in hot],"recentComments":[{"id":c.id,"content":c.content,"nickname":c.nickname,"status":c.status,"createdAt":c.created_at.isoformat()} for c in recent]})

@router.get("/dashboard/views-trend")
def views_trend(days: int = Query(7,ge=1,le=90), db: Session=Depends(get_db), user: User=Depends(require_editor)):
    start_day=date.today()-timedelta(days=days-1)
    since=datetime.combine(start_day,time.min)
    rows=db.execute(select(func.date(MetricEvent.created_at),func.count(MetricEvent.id)).where(MetricEvent.created_at>=since,MetricEvent.kind=="view").group_by(func.date(MetricEvent.created_at))).all()
    counts={str(day):count for day,count in rows}
    return envelope([{"date":(start_day+timedelta(days=i)).isoformat(),"views":counts.get((start_day+timedelta(days=i)).isoformat(),0)} for i in range(days)])

@router.get("/audit-logs")
def audit_logs(page: int = 1, pageSize: int = 20, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    q = select(AuditLog); total = db.scalar(select(func.count()).select_from(AuditLog)) or 0
    rows = db.scalars(q.order_by(AuditLog.created_at.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
    return envelope({"list":[{"id":x.id,"operatorId":x.operator_id,"username":x.username,"action":x.action,"path":x.path,"ip":x.ip,"createdAt":x.created_at.isoformat()} for x in rows],"total":total,"page":page,"pageSize":pageSize})

@router.delete("/audit-logs")
def clear_audit(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    db.query(AuditLog).delete(); db.commit(); return envelope({})

@router.post("/upload")
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_editor)):
    data = await file.read(image_service.MAX_UPLOAD_SIZE + 1)
    return envelope(image_service.save_image(db, file.filename, data, user.id))

@router.get("/images")
def images(db: Session = Depends(get_db), user: User = Depends(require_editor), page: int = Query(1, ge=1), pageSize: int = Query(24, ge=1, le=100)):
    q=select(Image); total=db.scalar(select(func.count(Image.id))) or 0
    rows=db.scalars(q.order_by(Image.created_at.desc(), Image.id.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
    return envelope({"list":[{"id":x.id,"url":x.url,"name":x.name,"size":x.size,"ext":x.ext,"uploaderId":x.uploader_id,"createdAt":x.created_at.isoformat()} for x in rows],"total":total,"page":page,"pageSize":pageSize})

@router.delete("/images/{image_id}")
def delete_image(image_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    image_service.delete_image(db, image_id)
    return envelope({})

@router.get("/links")
def links(db: Session = Depends(get_db), user: User = Depends(require_editor)):
    return envelope([{"id":x.id,"name":x.name,"url":x.url,"description":x.description,"sort":x.sort,"enabled":x.enabled} for x in db.scalars(select(Link).where(Link.deleted_at.is_(None)).order_by(Link.sort)).all()])

@router.post("/links", status_code=201)
def create_link(body: LinkInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x=Link(**body.model_dump()); db.add(x); db.commit(); db.refresh(x)
    return envelope({"id":x.id,"name":x.name,"url":x.url})

@router.put("/links/{item_id}")
def update_link(item_id: int, body: LinkInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x=db.get(Link,item_id)
    if not x or x.deleted_at: raise DomainError("link not found",404)
    for key,value in body.model_dump().items(): setattr(x,key,value)
    db.commit(); return envelope({"id":x.id,"name":x.name,"url":x.url})

@router.delete("/links/{item_id}")
def delete_link(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x=db.get(Link,item_id)
    if not x or x.deleted_at: raise DomainError("link not found",404)
    x.deleted_at=datetime.utcnow(); db.commit(); return envelope({})
