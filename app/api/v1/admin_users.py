from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin
from app.core.errors import DomainError, envelope
from app.models import User
from app.schemas.common import UserRoleInput, UserStatusInput
from app.services import users as user_service

router = APIRouter(prefix="/admin")


@router.get("/users")
def users(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=100),
    role: int | None = Query(None, ge=1, le=3),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    query = select(User).where(User.deleted_at.is_(None))
    if keyword.strip():
        term = keyword.strip()
        query = query.where(or_(User.username.contains(term), User.email.contains(term)))
    if role is not None:
        query = query.where(User.role == role)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(User.id.desc()).offset((page - 1) * pageSize).limit(pageSize)).all()
    return envelope({
        "list": [{"id": row.id, "username": row.username, "email": row.email,
                  "nickname": row.nickname, "role": row.role, "status": row.status,
                  "createdAt": row.created_at.isoformat()} for row in rows],
        "total": total, "page": page, "pageSize": pageSize,
    })


@router.put("/users/{user_id}/status")
def set_user_status(
    body: UserStatusInput,
    user_id: int = Path(gt=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        target = user_service.set_status(db, user.id, user_id, body.status)
    except user_service.UserManagementError as exc:
        raise DomainError(exc.message, exc.status_code) from exc
    return envelope({"id": target.id, "status": target.status})


@router.put("/users/{user_id}/role")
def set_user_role(
    body: UserRoleInput,
    user_id: int = Path(gt=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        target = user_service.set_role(db, user.id, user_id, body.role)
    except user_service.UserManagementError as exc:
        raise DomainError(exc.message, exc.status_code) from exc
    return envelope({"id": target.id, "role": target.role})
