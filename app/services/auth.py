from datetime import datetime, timedelta
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.security import hash_password, verify_and_update_password, new_token_pair, token_hash
from app.models import RefreshToken, User


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code


def register(db: Session, username: str, email: str, password: str) -> tuple[User, str, str]:
    if not email:
        raise AuthError("email is required")
    exists = db.scalar(select(User).where(or_(User.username == username, User.email == email)))
    if exists:
        raise AuthError("username or email already exists", 409)
    user = User(username=username, email=email, password_hash=hash_password(password), nickname=username)
    try:
        db.add(user)
        db.flush()
        access, refresh, digest = new_token_pair(user.id)
        db.add(RefreshToken(token_hash=digest, user_id=user.id, expires_at=datetime.utcnow() + timedelta(days=settings.refresh_token_days)))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        duplicate = db.scalar(select(User.id).where(or_(User.username == username, User.email == email)))
        if duplicate:
            raise AuthError("username or email already exists", 409) from exc
        raise
    return user, access, refresh


def login(db: Session, account: str, password: str, admin: bool = False) -> tuple[User, str, str]:
    user = db.scalar(select(User).where(User.deleted_at.is_(None), or_(User.username == account, User.email == account)).with_for_update())
    if not user or not user.status:
        raise AuthError("invalid credentials", 401)
    valid, upgraded_hash = verify_and_update_password(password, user.password_hash)
    if not valid:
        raise AuthError("invalid credentials", 401)
    if admin and user.role < 2:
        raise AuthError("editor role required", 403)
    if upgraded_hash:
        user.password_hash = upgraded_hash
    access, refresh, digest = new_token_pair(user.id)
    db.add(RefreshToken(token_hash=digest, user_id=user.id, expires_at=datetime.utcnow() + timedelta(days=settings.refresh_token_days)))
    db.commit()
    return user, access, refresh


def rotate_refresh(db: Session, raw_token: str) -> tuple[str, str]:
    from app.core.security import issue_token
    import jwt
    try:
        claims = jwt.decode(raw_token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer)
    except jwt.InvalidTokenError as exc:
        raise AuthError("invalid refresh token", 401) from exc
    if claims.get("type") != "refresh":
        raise AuthError("invalid token type", 401)
    now = datetime.utcnow()
    stored = db.get(RefreshToken, token_hash(raw_token))
    if not stored or stored.revoked_at or stored.expires_at < now:
        raise AuthError("refresh token expired or revoked", 401)
    user = db.scalar(select(User).where(User.id == stored.user_id).with_for_update())
    if not user or not user.status or user.deleted_at:
        db.rollback()
        raise AuthError("account unavailable", 401)
    # Conditional UPDATE makes rotation single-use even when two requests race.
    changed = db.execute(update(RefreshToken).where(RefreshToken.token_hash == token_hash(raw_token), RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > now).values(revoked_at=now)).rowcount
    if changed != 1:
        db.rollback()
        raise AuthError("refresh token expired or revoked", 401)
    access, refresh, digest = new_token_pair(user.id)
    db.add(RefreshToken(token_hash=digest, user_id=user.id, expires_at=now + timedelta(days=settings.refresh_token_days)))
    db.commit()
    return access, refresh


def revoke_refresh(db: Session, user_id: int, raw_token: str) -> None:
    # Lock the account before touching tokens, matching rotation and disable.
    db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    db.execute(update(RefreshToken).where(
        RefreshToken.token_hash == token_hash(raw_token),
        RefreshToken.user_id == user_id,
        RefreshToken.revoked_at.is_(None),
    ).values(revoked_at=datetime.utcnow()))
    db.commit()
