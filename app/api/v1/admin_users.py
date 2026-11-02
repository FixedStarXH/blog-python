from datetime import datetime, timedelta
from pathlib import Path
import secrets
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

router = APIRouter(prefix="/admin")

@router.get("/users")
def users(page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), keyword: str = "", role: int | None = None, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    q = select(User).where(User.deleted_at.is_(None))
    if keyword: q = q.where(User.username.contains(keyword) | User.email.contains(keyword))
    if role is not None: q = q.where(User.role == role)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(User.id.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
    return envelope({"list": [{"id": x.id,"username":x.username,"email":x.email,"nickname":x.nickname,"role":x.role,"status":x.status,"createdAt":x.created_at.isoformat()} for x in rows],"total":total,"page":page,"pageSize":pageSize})

@router.put("/users/{user_id}/status")
def set_user_status(user_id: int, body: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    x = db.get(User, user_id)
    if not x or x.deleted_at: raise DomainError("user not found", 404)
    x.status = int(body.get("status", 0))
    if x.status==0:
        from app.models import RefreshToken
        from sqlalchemy import update
        db.execute(update(RefreshToken).where(RefreshToken.user_id==x.id,RefreshToken.revoked_at.is_(None)).values(revoked_at=datetime.utcnow()))
    db.commit(); return envelope({"id":x.id,"status":x.status})

@router.put("/users/{user_id}/role")
def set_user_role(user_id: int, body: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    x = db.get(User, user_id)
    role = int(body.get("role", 1))
    if not x or x.deleted_at: raise DomainError("user not found", 404)
    if x.id == user.id or role not in (1,2,3): raise DomainError("invalid role change", 403)
    x.role = role; db.commit(); return envelope({"id":x.id,"role":x.role})
