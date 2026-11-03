from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher
from app.core.config import settings

password_hash = PasswordHash((Argon2Hasher(), BcryptHasher()))


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hash.verify(password, hashed)
    except (UnknownHashError, ValueError):
        return False


def verify_and_update_password(password: str, hashed: str) -> tuple[bool, str | None]:
    try:
        return password_hash.verify_and_update(password, hashed)
    except (UnknownHashError, ValueError):
        return False, None


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
