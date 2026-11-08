from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session
from app.api.deps import current_user, get_db
from app.core.errors import DomainError, envelope
from app.core.security import hash_password, verify_password
from app.models import RefreshToken, User
from app.schemas.common import APIModel, Credentials, TokenRefresh
from app.services import auth as auth_service

router = APIRouter()


class LoginInput(APIModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=128)


class ProfileInput(APIModel):
    nickname: str = Field(default="", max_length=50)
    avatar: str = Field(default="", max_length=255)
    bio: str = Field(default="", max_length=255)


class PasswordInput(APIModel):
    old_password: str = Field(alias="oldPassword", min_length=1, max_length=128)
    new_password: str = Field(alias="newPassword", min_length=6, max_length=128)


def token_data(user: User, access: str, refresh: str):
    return envelope({"accessToken": access, "refreshToken": refresh, "user": {"id": user.id, "username": user.username, "email": user.email, "nickname": user.nickname, "avatar": user.avatar, "role": user.role}})


@router.post("/auth/register")
def register(body: Credentials, db: Session = Depends(get_db)):
    if not body.email:
        raise DomainError("email is required")
    try:
        user, access, refresh = auth_service.register(db, body.username, body.email, body.password)
    except auth_service.AuthError as exc:
        raise DomainError(exc.message, exc.status_code) from exc
    return token_data(user, access, refresh)


@router.post("/auth/login")
def login(body: LoginInput, db: Session = Depends(get_db)):
    try:
        user, access, refresh = auth_service.login(db, body.username, body.password)
    except auth_service.AuthError as exc:
        raise DomainError(exc.message, exc.status_code) from exc
    return token_data(user, access, refresh)


@router.post("/admin/login")
def admin_login(body: LoginInput, db: Session = Depends(get_db)):
    try:
        user, access, refresh = auth_service.login(db, body.username, body.password, admin=True)
    except auth_service.AuthError as exc:
        raise DomainError(exc.message, exc.status_code) from exc
    return token_data(user, access, refresh)


@router.post("/auth/refresh")
def refresh(body: TokenRefresh, db: Session = Depends(get_db)):
    try:
        access, new_refresh = auth_service.rotate_refresh(db, body.refresh_token)
        import jwt
        claims = jwt.decode(access, auth_service.settings.jwt_secret, algorithms=["HS256"], issuer=auth_service.settings.jwt_issuer)
        user = db.get(User, int(claims["sub"]))
    except auth_service.AuthError as exc:
        raise DomainError(exc.message, exc.status_code) from exc
    if not user or not user.status:
        raise DomainError("account unavailable", 401)
    return token_data(user, access, new_refresh)


@router.post("/auth/logout")
def logout(body: TokenRefresh, user: User = Depends(current_user), db: Session = Depends(get_db)):
    auth_service.revoke_refresh(db, user.id, body.refresh_token)
    return envelope({})


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return envelope({"id": user.id, "username": user.username, "email": user.email, "nickname": user.nickname, "avatar": user.avatar, "bio": user.bio, "role": user.role})


@router.put("/auth/me")
def update_me(body: ProfileInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.nickname, user.avatar, user.bio = body.nickname, body.avatar, body.bio
    user.updated_at = datetime.utcnow()
    db.commit()
    return me(user)


@router.put("/auth/password")
@router.put("/admin/password")
def change_password(body: PasswordInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    if not user or not user.status or user.deleted_at:
        raise DomainError("account unavailable", 401)
    if not verify_password(body.old_password, user.password_hash):
        raise DomainError("old password is incorrect", 400)
    user.password_hash = hash_password(body.new_password)
    user.updated_at = datetime.utcnow()
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=datetime.utcnow()))
    db.commit()
    return envelope({})
