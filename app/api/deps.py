from collections.abc import Generator
from fastapi import Depends, Header
from sqlalchemy.orm import Session
import jwt
from app.core.config import settings
from app.core.errors import DomainError
from app.db.session import SessionLocal
from app.models import User


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as db:
        yield db


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise DomainError("authentication required", 401)
    try:
        claims = jwt.decode(authorization[7:], settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer)
        if claims.get("type") != "access":
            raise DomainError("invalid token type", 401)
        user = db.get(User, int(claims["sub"]))
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise DomainError("invalid access token", 401) from exc
    if not user or not user.status or user.deleted_at:
        raise DomainError("account unavailable", 401)
    return user


def optional_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User | None:
    if not authorization:
        return None
    return current_user(authorization, db)


def require_editor(user: User = Depends(current_user)) -> User:
    if user.role < 2:
        raise DomainError("editor role required", 403)
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role < 3:
        raise DomainError("admin role required", 403)
    return user
