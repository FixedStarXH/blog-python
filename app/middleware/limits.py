from collections import defaultdict, deque
from datetime import datetime, timezone
import logging
import time
from fastapi import Request
from fastapi.responses import JSONResponse
from app.core.errors import envelope

logger=logging.getLogger(__name__)
_fallback=defaultdict(deque)


def bucket_for(path: str, method: str):
    if path in {"/api/auth/login","/api/auth/register","/api/admin/login"}: return "auth",10,60
    if path.startswith("/api/ai/") or path.startswith("/api/admin/ai/") or path.endswith("/summary") or path.endswith("/polish"): return "ai",10,60
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
            if group=="ai":
                day=datetime.now(timezone.utc).strftime("%Y%m%d")
                daily=await redis_client.incr(f"blog:ai:daily:{ip}:{day}")
                if daily==1: await redis_client.expire(f"blog:ai:daily:{ip}:{day}",86400)
                allowed=allowed and daily<=100
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
