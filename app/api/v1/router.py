from fastapi import APIRouter
from app.api.v1 import auth, articles, taxonomy, comments, admin, public, ai, admin_ai, upload, columns

router = APIRouter(prefix="/api")
router.include_router(auth.router)
router.include_router(public.router)
router.include_router(articles.router)
router.include_router(taxonomy.router)
router.include_router(comments.router)
router.include_router(admin.router)
router.include_router(ai.router)
router.include_router(admin_ai.router)
router.include_router(upload.router)
router.include_router(columns.router)
