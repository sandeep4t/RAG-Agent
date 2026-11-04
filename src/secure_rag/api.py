import hmac
import logging
import time
import uuid
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from .audit import AuditLog
from .config import get_settings
from .metrics import REQUEST_SECONDS, REQUESTS
from .rag import RagService, Source
from .security import Post, digest, validate_post

app = FastAPI(title="Secure Local RAG Demo", version="0.1.0")
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


@lru_cache
def audit_log() -> AuditLog:
    return AuditLog(get_settings().audit_db)


class AuditMiddleware:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return
        log = audit_log()
        request_id = str(uuid.uuid4())
        state = scope.setdefault("state", {})
        state["audit_actor"] = "anonymous"
        started = time.perf_counter()
        response_status = 500

        async def send_with_id(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.application(scope, receive, send_with_id)
        except Exception as error:
            logger.exception("Unhandled API error")
            state.setdefault("audit_details", {})["error_type"] = type(error).__name__
            await JSONResponse({"detail": "internal server error"}, status_code=500)(
                scope, receive, send_with_id,
            )
        finally:
            duration = time.perf_counter() - started
            route = scope.get("route")
            route_name = route.path if route else "unmatched"
            REQUESTS.labels(scope["method"], route_name, str(response_status)).inc()
            REQUEST_SECONDS.labels(scope["method"], route_name).observe(duration)
            client = scope.get("client")
            log.record(
                request_id=request_id, method=scope["method"], path=scope["path"][:2048],
                status=response_status, duration_ms=duration * 1000,
                client_ip=client[0] if client else None, actor=state["audit_actor"],
                details=state.get("audit_details", {}),
            )


app.add_middleware(AuditMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def chat_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@lru_cache
def service() -> RagService:
    return RagService(get_settings())


def require_write_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().write_key
    if not expected or not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid write key")
    request.state.audit_actor = "shared_write_key"


class Query(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=10)


class SourceResponse(BaseModel):
    post_id: str
    title: str
    author: str
    text: str
    score: float


class AnswerResponse(BaseModel):
    answer: str
    sources: list[SourceResponse]


def source_responses(sources: list[Source]) -> list[SourceResponse]:
    return [SourceResponse(**source.__dict__) for source in sources]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.put("/posts/{post_id}", dependencies=[Depends(require_write_key)])
def put_post(post_id: str, post: Post, request: Request):
    request.state.audit_details = {"operation": "put_post", "post": post.model_dump(),
                                   "post_digest": digest(post)}
    if post.id != post_id:
        raise HTTPException(400, "path and body ids differ")
    try:
        validate_post(post, get_settings().authors)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    rag = service()
    rag.store.put(post)
    try:
        rag.index(post)
    except Exception as error:
        raise HTTPException(503, "post saved; indexing failed; run /admin/reindex") from error
    return {"id": post.id, "indexed": True}


@app.delete("/posts/{post_id}", dependencies=[Depends(require_write_key)])
def delete_post(post_id: str, request: Request):
    request.state.audit_details = {"operation": "delete_post", "post_id": post_id}
    rag = service()
    try:
        removed = rag.store.delete(post_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if not removed:
        raise HTTPException(404, "post not found")
    try:
        rag.remove(post_id)
    except Exception as error:
        raise HTTPException(503, "post deleted; index cleanup failed; run /admin/reindex") from error
    return {"id": post_id, "deleted": True}


@app.post("/admin/reindex", dependencies=[Depends(require_write_key)])
def reindex(request: Request):
    request.state.audit_details = {"operation": "reindex"}
    try:
        count = service().reconcile()
        request.state.audit_details["posts"] = count
        return {"posts": count}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/search", response_model=list[SourceResponse])
def search(request: Query, http_request: Request):
    http_request.state.audit_details = {"query": request.query, "limit": request.limit}
    sources = source_responses(service().search(request.query, request.limit))
    http_request.state.audit_details["sources"] = [source.model_dump() for source in sources]
    return sources


@app.post("/answer", response_model=AnswerResponse)
def answer(request: Query, http_request: Request):
    http_request.state.audit_details = {"query": request.query, "limit": request.limit}
    result, sources = service().answer(request.query, limit=request.limit)
    response = AnswerResponse(answer=result, sources=source_responses(sources))
    http_request.state.audit_details.update(response.model_dump())
    return response


@app.post("/insights", response_model=AnswerResponse)
def insights(request: Query, http_request: Request):
    http_request.state.audit_details = {"query": request.query, "limit": request.limit}
    result, sources = service().answer(request.query, insight=True, limit=request.limit)
    response = AnswerResponse(answer=result, sources=source_responses(sources))
    http_request.state.audit_details.update(response.model_dump())
    return response
