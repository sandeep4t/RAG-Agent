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
from .auth import Identity, IdentityStore
from .config import get_settings
from .metrics import REQUEST_SECONDS, REQUESTS
from .rag import RagService, Source
from .security import Post, digest, validate_post

app = FastAPI(title="Secure Local RAG Demo", version="0.1.0")
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


@lru_cache
def audit_log() -> AuditLog:
    settings = get_settings()
    return AuditLog(settings.audit_db, settings.audit_key_file)


@lru_cache
def identity_store() -> IdentityStore:
    return IdentityStore(get_settings().api_keys_file)


def audit_intent(request: Request, details: dict) -> None:
    client = request.client
    audit_log().record(
        request_id=request.state.request_id, phase="intent", method=request.method,
        path=request.url.path, status=None, duration_ms=None,
        client_ip=client.host if client else None, actor=request.state.audit_actor,
        details=details,
    )


class AuditMiddleware:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        state = scope.setdefault("state", {})
        state["audit_actor"] = "anonymous"
        state["request_id"] = request_id
        started = time.perf_counter()
        response_status = 500
        client = scope.get("client")
        client_ip = client[0] if client else None
        path = scope["path"][:2048]
        try:
            log = audit_log()
            log.record(request_id=request_id, phase="start", method=scope["method"],
                       path=path, status=None, duration_ms=None, client_ip=client_ip,
                       actor="anonymous", details={})
        except Exception:
            logger.exception("Audit start failed")
            await JSONResponse({"detail": "audit unavailable"}, status_code=503,
                               headers={"X-Request-ID": request_id})(scope, receive, send)
            return
        buffered = []

        async def buffer_response(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            buffered.append(message)

        try:
            await self.application(scope, receive, buffer_response)
        except Exception as error:
            logger.exception("Unhandled API error")
            state.setdefault("audit_details", {})["error_type"] = type(error).__name__
            buffered.clear()
            await JSONResponse({"detail": "internal server error"}, status_code=500)(
                scope, receive, buffer_response,
            )
        duration = time.perf_counter() - started
        route = scope.get("route")
        route_name = route.path if route else "unmatched"
        try:
            log.record(
                request_id=request_id, phase="complete", method=scope["method"], path=path,
                status=response_status, duration_ms=duration * 1000,
                client_ip=client_ip, actor=state["audit_actor"],
                details=state.get("audit_details", {}),
            )
        except Exception:
            logger.exception("Audit completion failed")
            await JSONResponse({"detail": "audit unavailable"}, status_code=503,
                               headers={"X-Request-ID": request_id})(scope, receive, send)
            return
        REQUESTS.labels(scope["method"], route_name, str(response_status)).inc()
        REQUEST_SECONDS.labels(scope["method"], route_name).observe(duration)
        for message in buffered:
            await send(message)


app.add_middleware(AuditMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def chat_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@lru_cache
def service() -> RagService:
    return RagService(get_settings())


def require_identity(request: Request, x_api_key: str | None = Header(default=None)) -> Identity:
    identity = identity_store().authenticate(x_api_key)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
    request.state.audit_actor = identity.name
    return identity


def require_editor(request: Request, x_api_key: str | None = Header(default=None)) -> Identity:
    identity = require_identity(request, x_api_key)
    if identity.role != "editor":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="editor role required")
    return identity


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


@app.put("/posts/{post_id}", dependencies=[Depends(require_editor)])
def put_post(post_id: str, post: Post, request: Request):
    request.state.audit_details = {"operation": "put_post", "post": post.model_dump(),
                                   "post_digest": digest(post)}
    if post.id != post_id:
        raise HTTPException(400, "path and body ids differ")
    if post.author != request.state.audit_actor:
        raise HTTPException(403, "post author must match authenticated editor")
    try:
        validate_post(post, get_settings().authors)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    audit_intent(request, request.state.audit_details)
    rag = service()
    rag.store.put(post)
    try:
        rag.index(post)
    except Exception as error:
        raise HTTPException(503, "post saved; indexing failed; run /admin/reindex") from error
    return {"id": post.id, "indexed": True}


@app.delete("/posts/{post_id}", dependencies=[Depends(require_editor)])
def delete_post(post_id: str, request: Request):
    request.state.audit_details = {"operation": "delete_post", "post_id": post_id}
    audit_intent(request, request.state.audit_details)
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


@app.post("/admin/reindex", dependencies=[Depends(require_editor)])
def reindex(request: Request):
    request.state.audit_details = {"operation": "reindex"}
    audit_intent(request, request.state.audit_details)
    try:
        count = service().reconcile()
        request.state.audit_details["posts"] = count
        return {"posts": count}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/search", response_model=list[SourceResponse], dependencies=[Depends(require_identity)])
def search(request: Query, http_request: Request):
    trace = {}
    http_request.state.audit_details = {"query": request.query, "limit": request.limit,
                                        "trace": trace}
    sources = source_responses(service().search(request.query, request.limit, trace=trace))
    http_request.state.audit_details["sources"] = [source.model_dump() for source in sources]
    return sources


@app.post("/answer", response_model=AnswerResponse, dependencies=[Depends(require_identity)])
def answer(request: Query, http_request: Request):
    trace = {}
    http_request.state.audit_details = {"query": request.query, "limit": request.limit,
                                        "trace": trace}
    result, sources = service().answer(request.query, limit=request.limit, trace=trace)
    response = AnswerResponse(answer=result, sources=source_responses(sources))
    http_request.state.audit_details.update(response.model_dump())
    return response


@app.post("/insights", response_model=AnswerResponse, dependencies=[Depends(require_identity)])
def insights(request: Query, http_request: Request):
    trace = {}
    http_request.state.audit_details = {"query": request.query, "limit": request.limit,
                                        "trace": trace}
    result, sources = service().answer(request.query, insight=True, limit=request.limit, trace=trace)
    response = AnswerResponse(answer=result, sources=source_responses(sources))
    http_request.state.audit_details.update(response.model_dump())
    return response
