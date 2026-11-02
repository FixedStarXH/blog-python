from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import jwt
from pwdlib import PasswordHash
from app.core.config import settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def issue_token(user_id: int, token_type: str, lifetime: timedelta, jti: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": str(user_id), "type": token_type, "iat": now, "exp": now + lifetime, "iss": settings.jwt_issuer}
    if jti:
        claims["jti"] = jti
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def new_token_pair(user_id: int) -> tuple[str, str, str]:
    access = issue_token(user_id, "access", timedelta(minutes=settings.access_token_minutes))
    raw_refresh = secrets.token_urlsafe(48)
    refresh = issue_token(user_id, "refresh", timedelta(days=settings.refresh_token_days), jti=raw_refresh)
    return access, refresh, hashlib.sha256(refresh.encode()).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
