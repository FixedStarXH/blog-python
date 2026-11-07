from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import RefreshToken, User


class UserManagementError(Exception):
    def __init__(self, message: str, status_code: int):
        self.message = message
        self.status_code = status_code


def _lock_target(db: Session, target_id: int) -> User:
    target = db.scalar(
        select(User).where(User.id == target_id, User.deleted_at.is_(None))
        .with_for_update().execution_options(populate_existing=True)
    )
    if target is None:
        raise UserManagementError("user not found", 404)
    return target


def set_status(db: Session, operator_id: int, target_id: int, status: int) -> User:
    if operator_id == target_id:
        raise UserManagementError("cannot change your own account status", 403)
    target = _lock_target(db, target_id)
    target.status = status
    target.updated_at = datetime.utcnow()
    if status == 0:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == target_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.utcnow())
        )
    db.commit()
    return target


def set_role(db: Session, operator_id: int, target_id: int, role: int) -> User:
    if operator_id == target_id:
        raise UserManagementError("cannot change your own role", 403)
    target = _lock_target(db, target_id)
    target.role = role
    target.updated_at = datetime.utcnow()
    db.commit()
    return target
