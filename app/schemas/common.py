from typing import Annotated, Literal
from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, field_validator


class APIModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class CategoryInput(APIModel):
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=200)
    sort: int = Field(default=0, ge=-2147483648, le=2147483647)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class CategoryUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    sort: int | None = Field(default=None, ge=-2147483648, le=2147483647)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class TagInput(APIModel):
    name: str = Field(min_length=1, max_length=50)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class TagUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class ColumnInput(APIModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=255)
    cover: str = Field(default="", max_length=255)
    status: Literal[0, 1] = 1

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class ColumnUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    cover: str | None = Field(default=None, max_length=255)
    status: Literal[0, 1] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


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
    tag_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, alias="tagIds")
    column_id: int | None = Field(default=None, alias="columnId", gt=0)
    publish_at: str | None = Field(default=None, alias="publishAt")
    is_top: bool = Field(default=False, alias="isTop")
    is_featured: bool = Field(default=False, alias="isFeatured")


class AdminArticleInput(ArticleInput):
    status: int = Field(default=1, ge=0, le=4)


class ArticleRejectInput(APIModel):
    reason: str = Field(min_length=1, max_length=255)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rejection reason cannot be blank")
        return value.strip()


class ArticleBatchInput(APIModel):
    ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1)
    action: Literal["publish", "approve", "draft", "setDraft", "top", "untop", "delete"] | None = None
    operation: Literal["publish", "approve", "draft", "setDraft", "top", "untop", "delete"] | None = None


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
    refresh_token: str = Field(alias="refreshToken", min_length=1)


class UserStatusInput(APIModel):
    status: Literal[0, 1]


class UserRoleInput(APIModel):
    role: Literal[1, 2, 3]
