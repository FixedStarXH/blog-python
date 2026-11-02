from fastapi import APIRouter
from app.api.v1.admin_articles import router as articles_router
from app.api.v1.admin_users import router as users_router
from app.api.v1.admin_comments import router as comments_router
from app.api.v1.admin_operations import router as operations_router

router=APIRouter()
router.include_router(articles_router)
router.include_router(users_router)
router.include_router(comments_router)
router.include_router(operations_router)
