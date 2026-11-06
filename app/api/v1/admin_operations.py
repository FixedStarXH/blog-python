from datetime import datetime, timedelta
from pathlib import Path
import secrets
from io import BytesIO
from fastapi import APIRouter, Depends, File, UploadFile, Query
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
from app.storage.files import remove_upload

router = APIRouter(prefix="/admin")

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
    values={x.key:("" if any(w in x.key.lower() for w in ("secret","password","api_key","apikey")) else x.value) for x in db.scalars(select(SiteSetting)).all()}
    raw=values.get("daily_quotes","")
    values["quotes"]=[{"content":part.split("|",1)[0].strip(),"author":part.split("|",1)[1].strip() if "|" in part and part.split("|",1)[1].strip() else "佚名"} for part in raw.splitlines() if part.strip()]
    return envelope(values)

@router.put("/settings")
def update_settings(body: dict, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    allowed={"site_title","site_subtitle","site_description","site_logo","site_beian","social_github","social_email"}
    values={key:str(body[key]) for key in allowed if key in body}
    if "quotes" in body:
        values["daily_quotes"]="\n".join(f"{str(q.get('content','')).strip()} | {str(q.get('author','佚名')).strip() or '佚名'}" for q in body["quotes"] if isinstance(q,dict) and str(q.get("content","")).strip())
    for key, value in values.items():
        item = db.get(SiteSetting, str(key))
        if item: item.value = str(value)
        else: db.add(SiteSetting(key=str(key), value=str(value)))
    db.commit(); return envelope({"updated":True})

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(require_editor)):
    hot=db.scalars(select(Article).where(Article.status==1,Article.deleted_at.is_(None),Article.password=="").order_by(Article.view_count.desc()).limit(5)).all()
    recent=db.scalars(select(Comment).where(Comment.deleted_at.is_(None)).order_by(Comment.created_at.desc()).limit(5)).all()
    counts={"articles":db.scalar(select(func.count(Article.id)).where(Article.deleted_at.is_(None))) or 0,"views":db.scalar(select(func.coalesce(func.sum(Article.view_count),0)).where(Article.deleted_at.is_(None))) or 0,"comments":db.scalar(select(func.count(Comment.id)).where(Comment.deleted_at.is_(None))) or 0,"pendingComments":db.scalar(select(func.count(Comment.id)).where(Comment.status==0,Comment.deleted_at.is_(None))) or 0}
    return envelope({"counts":counts,"hotArticles":[article_data(a,False) for a in hot],"recentComments":[{"id":c.id,"content":c.content,"nickname":c.nickname,"status":c.status,"createdAt":c.created_at.isoformat()} for c in recent]})

@router.get("/dashboard/views-trend")
def views_trend(days: int = Query(7,ge=1,le=90), db: Session=Depends(get_db), user: User=Depends(require_editor)):
    since=datetime.utcnow()-timedelta(days=days)
    rows=db.execute(select(func.date(MetricEvent.created_at),func.count(MetricEvent.id)).where(MetricEvent.created_at>=since,MetricEvent.kind=="view").group_by(func.date(MetricEvent.created_at)).order_by(func.date(MetricEvent.created_at))).all()
    return envelope([{"date":str(day),"views":count} for day,count in rows])

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
    allowed = {".jpg":b"\xff\xd8\xff", ".jpeg":b"\xff\xd8\xff", ".png":b"\x89PNG\r\n\x1a\n", ".gif":b"GIF8", ".webp":b"RIFF"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed: raise DomainError("unsupported image type", 415)
    data = await file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024 or not data.startswith(allowed[ext]): raise DomainError("invalid image content or size", 413)
    try:
        from PIL import Image as PILImage
        with PILImage.open(BytesIO(data)) as image:
            image.verify()
            expected={"jpg":"JPEG","jpeg":"JPEG","png":"PNG","gif":"GIF","webp":"WEBP"}[ext.lstrip(".")]
            if image.format!=expected: raise DomainError("image extension does not match its content",415)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError("image content is corrupt",415) from exc
    root = Path(settings.upload_dir).resolve(); root.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16) + ext; (root / name).write_bytes(data)
    image = Image(name=name, url=f"/uploads/{name}", size=len(data), ext=ext.lstrip("."), uploader_id=user.id)
    db.add(image); db.commit(); db.refresh(image)
    return envelope({"id":image.id,"url":image.url,"name":image.name,"size":image.size})

@router.get("/images")
def images(db: Session = Depends(get_db), user: User = Depends(require_editor), page: int = 1, pageSize: int = 20):
    q=select(Image); total=db.scalar(select(func.count(Image.id))) or 0
    rows=db.scalars(q.order_by(Image.created_at.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
    return envelope({"list":[{"id":x.id,"url":x.url,"name":x.name,"size":x.size,"createdAt":x.created_at.isoformat()} for x in rows],"total":total,"page":page,"pageSize":pageSize})

@router.delete("/images/{image_id}")
def delete_image(image_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x=db.get(Image,image_id)
    if not x: raise DomainError("image not found",404)
    try:
        remove_upload(x.url, settings.upload_dir)
    except ValueError as exc:
        raise DomainError("invalid stored upload path", 400) from exc
    db.delete(x); db.commit(); return envelope({})

@router.get("/links")
def links(db: Session = Depends(get_db), user: User = Depends(require_editor)):
    return envelope([{"id":x.id,"name":x.name,"url":x.url,"description":x.description,"sort":x.sort,"enabled":x.enabled} for x in db.scalars(select(Link).where(Link.deleted_at.is_(None)).order_by(Link.sort)).all()])

@router.post("/links", status_code=201)
def create_link(body: dict, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x=Link(name=str(body.get("name","")),url=str(body.get("url","")),description=str(body.get("description","")),sort=int(body.get("sort",0)),enabled=bool(body.get("enabled",True))); db.add(x); db.commit(); db.refresh(x)
    return envelope({"id":x.id,"name":x.name,"url":x.url})

@router.put("/links/{item_id}")
def update_link(item_id: int, body: dict, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x=db.get(Link,item_id)
    if not x or x.deleted_at: raise DomainError("link not found",404)
    for key,attr in (("name","name"),("url","url"),("description","description"),("sort","sort"),("enabled","enabled")):
        if key in body: setattr(x,attr,body[key])
    db.commit(); return envelope({"id":x.id,"name":x.name,"url":x.url})

@router.delete("/links/{item_id}")
def delete_link(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x=db.get(Link,item_id)
    if not x or x.deleted_at: raise DomainError("link not found",404)
    x.deleted_at=datetime.utcnow(); db.commit(); return envelope({})
