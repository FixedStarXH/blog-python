from typing import Annotated, Literal
from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, field_validator


class APIModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class Credentials(APIModel):
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=6, max_length=128)
    email: str = Field(default="", max_length=100)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        try:
            validate_email(value, check_deliverability=False, test_environment=True)
        except EmailNotValidError as exc:
            raise ValueError("invalid email address") from exc
        return value


class ArticleInput(APIModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200_000)
    summary: str = Field(default="", max_length=500)
    cover_image: str = Field(default="", max_length=255, alias="coverImage")
    source_url: str = Field(default="", max_length=255, alias="sourceUrl")
    password: str = Field(default="", max_length=255)
    clear_password: bool = Field(default=False, alias="clearPassword")
    category_id: int = Field(alias="categoryId", gt=0)
    tag_ids: list[int] = Field(default_factory=list, alias="tagIds")
    column_id: int | None = Field(default=None, alias="columnId")
    publish_at: str | None = Field(default=None, alias="publishAt")
    is_top: bool = Field(default=False, alias="isTop")
    is_featured: bool = Field(default=False, alias="isFeatured")


class AdminArticleInput(ArticleInput):
    status: int = Field(default=1, ge=0, le=4)


class ArticleUnlockInput(APIModel):
    password: str = Field(min_length=1)


class CommentInput(APIModel):
    content: str = Field(min_length=1, max_length=1000)
    nickname: str = Field(default="", max_length=20)
    parent_id: int | None = Field(default=None, alias="parentId", gt=0)

    @field_validator("content")
    @classmethod
    def nonblank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("comment cannot be blank")
        return value


class CommentStatusInput(APIModel):
    status: Literal[0, 1, 2]


class CommentBatchInput(APIModel):
    ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1)
    action: Literal["approve", "delete"]


class TokenRefresh(APIModel):
    refresh_token: str = Field(alias="refreshToken")
