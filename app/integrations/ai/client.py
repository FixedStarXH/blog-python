import httpx
import json
import math
from app.core.config import settings
from app.core.errors import DomainError


class EmbeddingUnsupported(DomainError):
    """Provider explicitly reported that it does not support embeddings."""

    def __init__(self):
        super().__init__("Embedding is explicitly unsupported by the provider", 503)


def _provider_http_error(status_code: int) -> DomainError:
    if status_code in {401, 403}:
        return DomainError("AI 服务认证失败，请检查服务端凭据", 502)
    if status_code == 429:
        return DomainError("AI 服务繁忙或额度受限，请稍后重试", 503)
    if status_code >= 500:
        return DomainError("AI 服务暂时不可用", 502)
    return DomainError(f"AI 服务拒绝了请求（上游 HTTP {status_code}）", 502)


async def request_json(base: str, key: str, path: str, payload: dict):
    if not base or not key:
        raise DomainError("AI provider is not configured", 503)
    try:
        timeout=httpx.Timeout(settings.ai_timeout_seconds,connect=settings.ai_connect_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response=await client.post(base.rstrip("/")+path,headers={"Authorization":f"Bearer {key}"},json=payload)
        response.raise_for_status()
        try:
            return response.json()
        except (ValueError, TypeError) as exc:
            raise DomainError("AI 服务返回了无效 JSON", 502) from exc
    except httpx.HTTPStatusError as exc:
        if path == "/embeddings":
            try:
                body = exc.response.json()
                error = body.get("error", {}) if isinstance(body, dict) else {}
                code = error.get("code") or body.get("code") if isinstance(error, dict) else body.get("code")
                if str(code).lower() in {"embedding_not_supported", "embeddings_not_supported", "unsupported_embedding"}:
                    raise EmbeddingUnsupported() from exc
            except (ValueError, AttributeError, TypeError):
                pass
        raise _provider_http_error(exc.response.status_code) from exc
    except httpx.TimeoutException as exc:
        raise DomainError("AI 服务响应超时，请稍后重试", 504) from exc
    except httpx.HTTPError as exc:
        raise DomainError("AI 服务连接失败，请稍后重试", 502) from exc


async def chat(messages: list[dict], stream: bool=False):
    return await request_json(settings.chat_base_url,settings.chat_api_key,"/chat/completions",{"model":settings.chat_model,"messages":messages,"stream":stream})


async def open_chat_stream(messages: list[dict]):
    if not settings.chat_base_url or not settings.chat_api_key:
        raise DomainError("AI provider is not configured",503)
    client=httpx.AsyncClient(timeout=httpx.Timeout(settings.ai_timeout_seconds,connect=settings.ai_connect_timeout_seconds))
    try:
        request=client.build_request("POST",settings.chat_base_url.rstrip("/")+"/chat/completions",headers={"Authorization":f"Bearer {settings.chat_api_key}","Accept":"text/event-stream"},json={"model":settings.chat_model,"messages":messages,"stream":True})
        response=await client.send(request,stream=True)
        response.raise_for_status()
    except ValueError as exc:
        await client.aclose()
        raise DomainError("AI 服务地址或请求配置无效",502) from exc
    except httpx.HTTPStatusError as exc:
        await client.aclose()
        raise _provider_http_error(exc.response.status_code) from exc
    except httpx.TimeoutException as exc:
        await client.aclose()
        raise DomainError("AI 服务响应超时，请稍后重试",504) from exc
    except httpx.HTTPError as exc:
        await client.aclose()
        raise DomainError("AI 服务连接失败，请稍后重试",502) from exc

    async def events():
        completed=False
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data:"): continue
                data=line[5:].strip()
                if data=="[DONE]":
                    completed=True
                    yield "data: [DONE]\n\n"
                    return
                try: payload=json.loads(data)
                except json.JSONDecodeError: continue
                if not isinstance(payload,dict):
                    continue
                if payload.get("error"):
                    yield "data: "+json.dumps({"error":{"message":"AI 上游生成失败，请稍后重试"}},ensure_ascii=False)+"\n\n"
                    return
                choices=payload.get("choices")
                if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
                    continue
                delta_block=choices[0].get("delta",{})
                if not isinstance(delta_block,dict):
                    continue
                delta=delta_block.get("content")
                if isinstance(delta,str) and delta:
                    yield "data: "+json.dumps({"delta":delta},ensure_ascii=False)+"\n\n"
            if not completed:
                yield "data: "+json.dumps({"error":{"message":"AI 上游连接中断，当前回答可能不完整"}},ensure_ascii=False)+"\n\n"
        except httpx.TimeoutException:
            yield "data: "+json.dumps({"error":{"message":"AI 服务响应超时，当前回答可能不完整"}},ensure_ascii=False)+"\n\n"
        except httpx.HTTPError:
            yield "data: "+json.dumps({"error":{"message":"AI 上游连接中断，当前回答可能不完整"}},ensure_ascii=False)+"\n\n"
        finally:
            await response.aclose(); await client.aclose()
    return events()


async def embed(texts: list[str]):
    if not settings.embedding_base_url or not settings.embedding_api_key:
        raise DomainError("Embedding provider is not configured",503)
    data=await request_json(settings.embedding_base_url,settings.embedding_api_key,"/embeddings",{"model":settings.embedding_model,"input":texts})
    items=data.get("data") if isinstance(data,dict) else None
    if not isinstance(items,list):
        raise DomainError("invalid embedding response",502)
    vectors=[item.get("embedding") if isinstance(item,dict) else None for item in items]
    if len(vectors)!=len(texts) or any(not isinstance(v,list) for v in vectors):
        raise DomainError("invalid embedding response",502)
    dimension=settings.embedding_dimension or (len(vectors[0]) if vectors else 0)
    if dimension < 1 or any(len(v)!=dimension for v in vectors): raise DomainError("embedding dimension mismatch",502)
    if any(any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector) for vector in vectors):
        raise DomainError("embedding values must be finite numbers",502)
    return vectors
