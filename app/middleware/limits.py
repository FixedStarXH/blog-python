from collections import defaultdict, deque
from datetime import datetime, timezone
import logging
import time
from fastapi import Request
from fastapi.responses import JSONResponse
from app.core.errors import envelope
from app.core.config import settings

logger=logging.getLogger(__name__)
_fallback=defaultdict(deque)


def bucket_for(path: str, method: str):
    if path in {"/api/auth/login","/api/auth/register","/api/admin/login"}: return "auth",10,60
    if path.startswith("/api/ai/") or path.startswith("/api/admin/ai/") or path.endswith("/summary") or path.endswith("/polish"): return "ai",settings.ai_minute_limit,60
    if path.endswith("/comments") or "/comments/" in path: return "comments",20,60
    if method in {"POST","PUT","PATCH","DELETE"}: return "write",120,60
    return "read",600,60


async def enforce_rate_limit(request: Request, redis_client):
    path=request.url.path
    if not path.startswith("/api/"): return None
    ip=request.client.host if request.client else "unknown"
    group,limit,window=bucket_for(path,request.method)
    key=f"blog:rl:{group}:{ip}"
    allowed=True
    if redis_client:
        try:
            async with redis_client.pipeline(transaction=True) as pipe:
                pipe.incr(key); pipe.ttl(key)
                count,ttl=await pipe.execute()
            if ttl<0: await redis_client.expire(key,window)
            allowed=count<=limit
        except Exception:
            logger.warning("Redis limiter unavailable; using process-local fallback",exc_info=True)
            redis_client=None
    if not redis_client:
        now=time.monotonic(); queue=_fallback[key]
        while queue and queue[0]<=now-window: queue.popleft()
        queue.append(now); allowed=len(queue)<=limit
    if not allowed:
        return JSONResponse(status_code=429,content=envelope(None,"请求过于频繁，请稍后重试",429),headers={"Retry-After":str(window)})
    return None


async def consume_ai_daily_quota(redis_client, client_ip: str) -> bool | None:
    """Consume one validated AI operation; None means Redis quota is unavailable."""
    if redis_client is None:
        return None
    day=datetime.now(timezone.utc).strftime("%Y%m%d")
    key=f"blog:ai:daily:{client_ip}:{day}"
    try:
        count=await redis_client.incr(key)
        if count==1:
            await redis_client.expire(key,86400)
        return count<=settings.ai_daily_limit
    except Exception:
        logger.warning("Redis AI daily quota unavailable; allowing request under minute limits",exc_info=True)
        return None
