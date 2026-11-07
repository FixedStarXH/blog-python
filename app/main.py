from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
import logging
import json
import time
import uuid
import re
from datetime import datetime, timezone
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
from app.tasks.scheduler import publishing_loop, rag_maintenance_loop, view_flush_loop

logger=logging.getLogger("blog")


class JsonLogFormatter(logging.Formatter):
    def format(self,record):
        message=record.getMessage()
        try:
            payload=json.loads(message)
            if not isinstance(payload,dict): payload={"message":message}
        except (ValueError,TypeError):
            payload={"message":message}
        payload.setdefault("timestamp",datetime.fromtimestamp(record.created,timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z"))
        payload.setdefault("level",record.levelname)
        payload.setdefault("logger",record.name)
        return json.dumps(payload,separators=(",",":"),ensure_ascii=False)


logger.setLevel(logging.INFO)
logger.propagate=False
if not any(getattr(handler,"_blog_json_handler",False) for handler in logger.handlers):
    log_handler=logging.StreamHandler()
    log_handler._blog_json_handler=True
    log_handler.setFormatter(JsonLogFormatter())
    logger.addHandler(log_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_production()
    import redis.asyncio as redis
    app.state.redis=redis.from_url(settings.redis_url,decode_responses=True,socket_connect_timeout=1,socket_timeout=1)
    scheduler=asyncio.create_task(publishing_loop(app))
    rag_worker=asyncio.create_task(rag_maintenance_loop())
    view_worker=asyncio.create_task(view_flush_loop(app))
    yield
    scheduler.cancel()
    rag_worker.cancel()
    view_worker.cancel()
    try: await scheduler
    except asyncio.CancelledError: pass
    try: await rag_worker
    except asyncio.CancelledError: pass
    try: await view_worker
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
    supplied_id=request.headers.get("x-request-id","")
    request_id=supplied_id if re.fullmatch(r"[A-Za-z0-9._-]{1,64}",supplied_id) else uuid.uuid4().hex
    started=time.perf_counter()
    try:
        limited=await enforce_rate_limit(request,getattr(request.app.state,"redis",None))
    except Exception as exc:
        if _is_audited_request(request): _audit_request(request,500)
        _log_request(request,request_id,500,started,error=type(exc).__name__)
        response=JSONResponse(status_code=500,content=envelope(None,"internal server error",500))
        _set_response_headers(response,request_id)
        return response
    if limited:
        _set_response_headers(limited,request_id)
        if _is_audited_request(request): _audit_request(request,limited.status_code)
        _log_request(request,request_id,limited.status_code,started)
        return limited
    cache= getattr(request.app.state,"redis",None)
    cache_key=None
    if request.method=="GET" and request.headers.get("authorization") is None and _cacheable(request.url.path) and cache:
        try:
            version=await cache.get("blog:http:version") or "0"
            # A new namespace prevents older cached, unsanitized article bodies
            # from being served after the HTML filtering change is deployed.
            cache_key=f"blog:http:v2:{version}:{request.url.path}?{request.url.query}"
            cached=await cache.get(cache_key)
            if cached:
                item=json.loads(cached)
                response=JSONResponse(content=item["body"],status_code=item["status"],headers={"X-Cache":"HIT"})
                _set_response_headers(response,request_id)
                _log_request(request,request_id,response.status_code,started,cache="HIT")
                return response
        except Exception:
            logger.debug("Redis response cache unavailable",exc_info=True)
    request_error=None
    try:
        response=await call_next(request)
    except Exception as exc:
        request_error=type(exc).__name__
        response=JSONResponse(status_code=500,content=envelope(None,"internal server error",500))
    if _is_audited_request(request):
        response,body=await _buffer_response(response)
        _audit_request(request,response.status_code,body)
    _set_response_headers(response,request_id)
    _log_request(request,request_id,response.status_code,started,cache="MISS" if cache_key else None,error=request_error)
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
            _set_response_headers(response,request_id)
        except Exception: logger.debug("Redis response cache write failed",exc_info=True)
    return response


def _set_response_headers(response,request_id: str):
    response.headers["X-Request-ID"]=request_id
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"


def _log_request(request: Request,request_id: str,status_code: int,started: float,cache: str | None=None,error: str | None=None):
    record={"event":"http_request","request_id":request_id,"method":request.method,"path":request.url.path,
            "status_code":status_code,"duration_ms":round((time.perf_counter()-started)*1000,2),
            "client_ip":request.client.host if request.client else ""}
    if cache: record["cache"]=cache
    if error: record["error_type"]=error
    logger.info(json.dumps(record,separators=(",",":"),ensure_ascii=False))


def _is_audited_request(request: Request) -> bool:
    if request.method not in {"POST","PUT","PATCH","DELETE"}:
        return False
    path=request.url.path
    return path.startswith("/api/admin/") or path in {
        "/api/auth/login","/api/auth/register","/api/auth/logout","/api/auth/me",
        "/api/auth/password","/api/admin/login"
    }


async def _buffer_response(response):
    if hasattr(response,"body_iterator"):
        body=b"".join([chunk async for chunk in response.body_iterator])
    else:
        body=response.body
    response=Response(content=body,status_code=response.status_code,headers=dict(response.headers),background=response.background)
    return response,body


def _cacheable(path: str) -> bool:
    if path in {"/api/categories","/api/tags","/api/archive","/api/archives","/api/settings","/api/site","/api/links"}: return True
    return path=="/api/articles" or (path.startswith("/api/articles/") and path.count("/")==3)


def _audit_request(request: Request,status_code: int,response_body: bytes=b""):
    try:
        operator_id,username,role=0,"",0
        result_data={}
        if response_body:
            try:
                payload=json.loads(response_body)
                result_data=payload.get("data",{}) if isinstance(payload,dict) else {}
            except (ValueError,TypeError):
                result_data={}
        header=request.headers.get("authorization","")
        if header.lower().startswith("bearer "):
            claims=jwt.decode(header[7:],settings.jwt_secret,algorithms=["HS256"],issuer=settings.jwt_issuer)
            operator_id=int(claims.get("sub",0))
        elif status_code<400 and isinstance(result_data,dict) and result_data.get("accessToken"):
            claims=jwt.decode(result_data["accessToken"],settings.jwt_secret,algorithms=["HS256"],issuer=settings.jwt_issuer)
            operator_id=int(claims.get("sub",0))
        if operator_id:
            with SessionLocal() as lookup:
                user=lookup.get(User,operator_id)
                if user: username,role=user.username,user.role
        parts=[part for part in request.url.path.strip("/").split("/") if part]
        if len(parts)>=3 and parts[:2]==["api","admin"]:
            target_type="account" if parts[2] in {"login","password"} else parts[2]
            item_id=next((part for part in parts[3:] if part.isdigit()),"")
            if parts[3:4] and parts[3].isdigit(): item_id=parts[3]
            normalized=parts.copy()
            for index in range(3,len(normalized)):
                if normalized[index].isdigit(): normalized[index]="{id}"
            target_id=int(item_id) if item_id else 0
            if target_type=="account" and target_id==0: target_id=operator_id
        else:
            target_type="account" if len(parts)>1 and parts[1] in {"login","register","logout","me","password"} else (parts[1] if len(parts)>1 else "")
            target_id=operator_id
            normalized=parts.copy()
        action=f"{request.method.upper()} /"+"/".join(normalized)
        if status_code<400 and request.method.upper()=="POST" and isinstance(result_data,dict):
            created_id=result_data.get("id")
            if not isinstance(created_id,int) and isinstance(result_data.get("user"),dict):
                created_id=result_data["user"].get("id")
            if isinstance(created_id,int): target_id=created_id
        with SessionLocal() as db:
            db.add(AuditLog(operator_id=operator_id,username=username,role=role,action=action[:100],method=request.method[:10],path=request.url.path[:200],target_type=target_type[:50],target_id=target_id,detail=f"HTTP {status_code}",ip=request.client.host[:45] if request.client else "",status=1 if status_code<400 else 0))
            db.commit()
    except Exception as exc:
        logger.error(json.dumps({"event":"audit_write_failed","path":request.url.path[:200],"error_type":type(exc).__name__},separators=(",",":")))


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
    redis_client=None
    try:
        import redis
        redis_client=redis.Redis.from_url(settings.redis_url,socket_connect_timeout=1,socket_timeout=1)
        redis_client.ping()
    except Exception:
        components["redis"]="unavailable"
    finally:
        if redis_client:
            try: redis_client.close()
            except Exception: pass
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
