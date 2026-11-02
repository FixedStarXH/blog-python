import httpx
import json
from app.core.config import settings
from app.core.errors import DomainError


async def request_json(base: str, key: str, path: str, payload: dict):
    if not base or not key:
        raise DomainError("AI provider is not configured", 503)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0)) as client:
            response=await client.post(base.rstrip("/")+path,headers={"Authorization":f"Bearer {key}"},json=payload)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise DomainError(f"AI provider returned HTTP {exc.response.status_code}", 502) from exc
    except httpx.HTTPError as exc:
        raise DomainError("AI provider unavailable", 502) from exc


async def chat(messages: list[dict], stream: bool=False):
    return await request_json(settings.chat_base_url,settings.chat_api_key,"/chat/completions",{"model":settings.chat_model,"messages":messages,"stream":stream})


async def open_chat_stream(messages: list[dict]):
    if not settings.chat_base_url or not settings.chat_api_key:
        raise DomainError("AI provider is not configured",503)
    client=httpx.AsyncClient(timeout=httpx.Timeout(90.0,connect=10.0))
    request=client.build_request("POST",settings.chat_base_url.rstrip("/")+"/chat/completions",headers={"Authorization":f"Bearer {settings.chat_api_key}","Accept":"text/event-stream"},json={"model":settings.chat_model,"messages":messages,"stream":True})
    try:
        response=await client.send(request,stream=True)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        await client.aclose()
        raise DomainError(f"AI provider returned HTTP {exc.response.status_code}",502) from exc
    except httpx.HTTPError as exc:
        await client.aclose()
        raise DomainError("AI provider unavailable",502) from exc

    async def events():
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data:"): continue
                data=line[5:].strip()
                if data=="[DONE]":
                    yield "data: [DONE]\n\n"
                    return
                try: payload=json.loads(data)
                except json.JSONDecodeError: continue
                delta=payload.get("choices",[{}])[0].get("delta",{}).get("content")
                if delta: yield "data: "+json.dumps({"delta":delta},ensure_ascii=False)+"\n\n"
            yield "data: [DONE]\n\n"
        finally:
            await response.aclose(); await client.aclose()
    return events()


async def embed(texts: list[str]):
    if not settings.embedding_base_url or not settings.embedding_api_key:
        raise DomainError("Embedding provider is not configured",503)
    data=await request_json(settings.embedding_base_url,settings.embedding_api_key,"/embeddings",{"model":settings.embedding_model,"input":texts})
    vectors=[item.get("embedding") for item in data.get("data",[])]
    if len(vectors)!=len(texts) or any(not isinstance(v,list) for v in vectors):
        raise DomainError("invalid embedding response",502)
    dimension=settings.embedding_dimension or (len(vectors[0]) if vectors else 0)
    if any(len(v)!=dimension for v in vectors): raise DomainError("embedding dimension mismatch",502)
    return vectors
