from datetime import date, datetime, time, timedelta
from fastapi import APIRouter, Depends, File, UploadFile, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.api.deps import get_db, require_admin, require_editor
from app.core.config import settings
from app.core.errors import envelope
from app.models import Article, AuditLog, Category, Comment, Image, MetricEvent, Tag, User
from app.api.v1.articles import article_data
from app.services import admin_operations as admin_service
from app.services import images as image_service
from app.services import site_operations

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
    return envelope(site_operations.read_settings(db))

@router.put("/settings")
def update_settings(body: SiteSettingsInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    values={key:getattr(body,key) for key in ("site_title","site_subtitle","site_description","site_logo","site_beian","social_github","social_email") if key in body.model_fields_set}
    if "quotes" in body.model_fields_set:
        values["daily_quotes"]="\n".join(f"{q.content.strip()} | {q.author.strip() or '佚名'}" for q in body.quotes)
    site_operations.update_settings(db, values)
    return envelope({"updated":True})

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(require_editor)):
    return envelope(admin_service.dashboard(db))

@router.get("/dashboard/views-trend")
def views_trend(days: int = Query(7,ge=1,le=90), db: Session=Depends(get_db), user: User=Depends(require_editor)):
    return envelope(admin_service.views_trend(db, days))

@router.get("/audit-logs")
def audit_logs(page: int = Query(1,ge=1), pageSize: int = Query(20,ge=1,le=100), action: str | None = Query(None,max_length=100), status: int | None = Query(None,ge=0,le=1), operatorId: int | None = Query(None,ge=1), targetType: str | None = Query(None,max_length=50), db: Session = Depends(get_db), user: User = Depends(require_editor)):
    return envelope(admin_service.audit_logs(db, page, pageSize, action, status, operatorId, targetType))

@router.delete("/audit-logs")
def clear_audit(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    admin_service.clear_audit_logs(db)
    return envelope({})

@router.post("/upload")
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_editor)):
    data = await file.read(image_service.MAX_UPLOAD_SIZE + 1)
    return envelope(image_service.save_image(db, file.filename, data, user.id))

@router.get("/images")
def images(db: Session = Depends(get_db), user: User = Depends(require_editor), page: int = Query(1, ge=1), pageSize: int = Query(24, ge=1, le=100)):
    q=select(Image); total=db.scalar(select(func.count(Image.id))) or 0
    rows=db.scalars(q.order_by(Image.created_at.desc(), Image.id.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
    return envelope({"list":[{"id":x.id,"url":x.url,"name":x.name,"size":x.size,"ext":x.ext,"uploaderId":x.uploader_id,"uploader":{"id":x.uploader.id,"createdAt":x.uploader.created_at.isoformat(),"updatedAt":x.uploader.updated_at.isoformat(),"username":x.uploader.username,"email":x.uploader.email,"nickname":x.uploader.nickname,"avatar":x.uploader.avatar,"role":x.uploader.role,"status":x.uploader.status,"bio":x.uploader.bio},"createdAt":x.created_at.isoformat()} for x in rows],"total":total,"page":page,"pageSize":pageSize})

@router.delete("/images/{image_id}")
def delete_image(image_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    image_service.delete_image(db, image_id)
    return envelope({})

@router.get("/links")
def links(db: Session = Depends(get_db), user: User = Depends(require_editor)):
    return envelope([{"id":x.id,"name":x.name,"url":x.url,"description":x.description,"sort":x.sort,"enabled":x.enabled} for x in site_operations.list_links(db)])

@router.post("/links")
def create_link(body: LinkInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x=site_operations.create_link(db, body.model_dump())
    return envelope({"id":x.id,"name":x.name,"url":x.url})

@router.put("/links/{item_id}")
def update_link(item_id: int, body: LinkInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x=site_operations.update_link(db, item_id, body.model_dump())
    return envelope({"id":x.id,"name":x.name,"url":x.url})

@router.delete("/links/{item_id}")
def delete_link(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    site_operations.delete_link(db, item_id)
    return envelope({})
