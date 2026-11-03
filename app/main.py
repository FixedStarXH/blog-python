from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
import logging
import json
import time
import uuid
from fastapi import FastAPI, Request
from fastapi import Depends
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
import jwt
from app.api.v1.router import router as api_router
from app.api.v1.public import rss as api_rss, sitemap as api_sitemap
from app.api.deps import get_db
from app.core.config import settings
from app.core.errors import DomainError, domain_error_handler, envelope, validation_error_handler
from app.db.session import engine, SessionLocal
from app.models import AuditLog, User
from app.middleware.limits import enforce_rate_limit
from app.tasks.scheduler import publishing_loop, rag_maintenance_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger=logging.getLogger("blog")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_production()
    import redis.asyncio as redis
    app.state.redis=redis.from_url(settings.redis_url,decode_responses=True,socket_connect_timeout=1,socket_timeout=1)
    scheduler=asyncio.create_task(publishing_loop())
    rag_worker=asyncio.create_task(rag_maintenance_loop())
    yield
    scheduler.cancel()
    rag_worker.cancel()
    try: await scheduler
    except asyncio.CancelledError: pass
    try: await rag_worker
    except asyncio.CancelledError: pass
    await app.state.redis.aclose()
    engine.dispose()


app=FastAPI(title="Blog System API",version="1.0.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in settings.cors_origins.split(",") if x.strip()],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
app.add_exception_handler(DomainError,domain_error_handler)
app.add_exception_handler(RequestValidationError,validation_error_handler)
app.include_router(api_router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id=request.headers.get("x-request-id") or uuid.uuid4().hex
    started=time.perf_counter()
    limited=await enforce_rate_limit(request,getattr(request.app.state,"redis",None))
    if limited:
        limited.headers["X-Request-ID"]=request_id
        return limited
    cache= getattr(request.app.state,"redis",None)
    cache_key=None
    if request.method=="GET" and request.headers.get("authorization") is None and _cacheable(request.url.path) and cache:
        try:
            version=await cache.get("blog:http:version") or "0"
            cache_key=f"blog:http:{version}:{request.url.path}?{request.url.query}"
            cached=await cache.get(cache_key)
            if cached:
                item=json.loads(cached)
                return JSONResponse(content=item["body"],status_code=item["status"],headers={"X-Request-ID":request_id,"X-Cache":"HIT","X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"strict-origin-when-cross-origin"})
        except Exception:
            logger.debug("Redis response cache unavailable",exc_info=True)
    try:
        response=await call_next(request)
    except Exception:
        _audit_request(request,500)
        raise
    response.headers["X-Request-ID"]=request_id
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
    logger.info("request_id=%s method=%s path=%s status=%s duration_ms=%.1f",request_id,request.method,request.url.path,response.status_code,(time.perf_counter()-started)*1000)
    if request.method in {"POST","PUT","PATCH","DELETE"} and (request.url.path.startswith("/api/admin/") or request.url.path in {"/api/auth/login","/api/auth/register","/api/auth/logout","/api/admin/login"}):
        _audit_request(request,response.status_code)
    if request.method in {"POST","PUT","PATCH","DELETE"} and request.url.path.startswith("/api/") and cache:
        try: await cache.incr("blog:http:version")
        except Exception: logger.debug("Redis cache version update failed",exc_info=True)
    if cache_key and response.status_code==200:
        try:
            body=b"".join([chunk async for chunk in response.body_iterator])
            status=response.status_code
            response=Response(content=body,status_code=status,media_type="application/json")
            payload=json.loads(body)
            await cache.set(cache_key,json.dumps({"status":status,"body":payload},ensure_ascii=False),ex=60)
            response.headers["X-Cache"]="MISS"
            response.headers["X-Request-ID"]=request_id
            response.headers["X-Content-Type-Options"]="nosniff"
            response.headers["X-Frame-Options"]="DENY"
            response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
        except Exception: logger.debug("Redis response cache write failed",exc_info=True)
    return response


def _cacheable(path: str) -> bool:
    if path in {"/api/categories","/api/tags","/api/archive","/api/archives","/api/settings","/api/site","/api/links"}: return True
    return path=="/api/articles" or (path.startswith("/api/articles/") and path.count("/")==3)


def _audit_request(request: Request,status_code: int):
    try:
        operator_id,username,role=0,"",0
        header=request.headers.get("authorization","")
        if header.lower().startswith("bearer "):
            claims=jwt.decode(header[7:],settings.jwt_secret,algorithms=["HS256"],issuer=settings.jwt_issuer)
            operator_id=int(claims.get("sub",0))
            with SessionLocal() as lookup:
                user=lookup.get(User,operator_id)
                if user: username,role=user.username,user.role
        with SessionLocal() as db:
            db.add(AuditLog(operator_id=operator_id,username=username,role=role,action=f"{request.method.lower()} {request.url.path}",method=request.method,path=request.url.path,target_type="",target_id=0,detail="",ip=request.client.host if request.client else "",status=1 if status_code<400 else 0))
            db.commit()
    except Exception:
        logger.exception("failed to persist audit event path=%s",request.url.path)


@app.get("/health")
def health():
    return {"status":"ok"}


@app.get("/ready")
def ready():
    components={"database":"ok","redis":"ok"}
    try:
        with engine.connect() as connection: connection.execute(text("SELECT 1"))
    except Exception:
        components["database"]="unavailable"
    try:
        import redis
        redis.Redis.from_url(settings.redis_url,socket_connect_timeout=1,socket_timeout=1).ping()
    except Exception:
        components["redis"]="unavailable"
    ok=all(v=="ok" for v in components.values())
    return JSONResponse(status_code=200 if ok else 503,content={"status":"ready" if ok else "not_ready","components":components})


@app.get("/feed.xml",include_in_schema=False)
def root_feed(db=Depends(get_db)):
    return api_rss(db)


@app.get("/sitemap.xml",include_in_schema=False)
def root_sitemap(db=Depends(get_db)):
    return api_sitemap(db)


@app.get("/",include_in_schema=False)
def frontend_index():
    path=Path("dist/index.html")
    return FileResponse(path) if path.is_file() else JSONResponse(status_code=503,content={"detail":"frontend build is missing"})


if Path("dist").is_dir():
    app.mount("/web",StaticFiles(directory="dist",html=True),name="web")
Path(settings.upload_dir).mkdir(parents=True,exist_ok=True)
app.mount("/uploads",StaticFiles(directory=settings.upload_dir),name="uploads")


@app.api_route("/{asset_path:path}",methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],include_in_schema=False)
def frontend_assets(asset_path: str, request: Request):
    if asset_path=="api" or asset_path.startswith("api/"):
        return JSONResponse(status_code=404,content=envelope(None,"接口不存在或未实现",404))
    if request.method != "GET":
        return JSONResponse(status_code=404,content={"detail":"Not Found"})
    candidate=(Path("dist")/asset_path).resolve()
    root=Path("dist").resolve()
    if root in candidate.parents and candidate.is_file(): return FileResponse(candidate)
    not_found=root/"404.html"
    if not_found.is_file(): return FileResponse(not_found,status_code=404)
    return JSONResponse(status_code=404,content={"detail":"Not Found"})
