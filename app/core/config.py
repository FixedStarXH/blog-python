from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BLOG_", env_file=".env", extra="ignore")
    env: str = "development"
    database_url: str = "sqlite:///./blog-python.db"
    jwt_secret: str = "development-only-change-me"
    jwt_issuer: str = "blog-system"
    access_token_minutes: int = 120
    refresh_token_days: int = 7
    redis_url: str = "redis://localhost:6379/0"
    public_url: str = "http://localhost:8080"
    upload_dir: str = "uploads"
    chat_base_url: str = ""
    chat_api_key: str = ""
    chat_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_dimension: int = 0
    seed_password: str = "123456"
    seed_username: str = "admin"
    seed_email: str = "admin@example.com"
    cors_origins: str = "http://localhost:5173"

    def validate_production(self) -> None:
        if self.env == "production":
            if len(self.jwt_secret) < 32 or "replace" in self.jwt_secret.lower() or "change-me" in self.jwt_secret.lower():
                raise RuntimeError("Set a unique BLOG_JWT_SECRET of at least 32 characters")
            if len(self.seed_password) < 12 or "replace" in self.seed_password.lower():
                raise RuntimeError("Set BLOG_SEED_PASSWORD to at least 12 characters")
            if self.seed_username == "admin" or self.seed_email.endswith(("example.com",".example")):
                raise RuntimeError("Set BLOG_SEED_USERNAME and BLOG_SEED_EMAIL for production")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
