import hmac
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from .config import get_settings
from .rag import RagService, Source
from .security import Post, validate_post

app = FastAPI(title="Secure Local RAG Demo", version="0.1.0")


@lru_cache
def service() -> RagService:
    return RagService(get_settings())


def require_write_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().write_key
    if not expected or not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid write key")


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


@app.put("/posts/{post_id}", dependencies=[Depends(require_write_key)])
def put_post(post_id: str, post: Post):
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
def delete_post(post_id: str):
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
def reindex():
    try:
        return {"posts": service().reconcile()}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/search", response_model=list[SourceResponse])
def search(request: Query):
    return source_responses(service().search(request.query, request.limit))


@app.post("/answer", response_model=AnswerResponse)
def answer(request: Query):
    result, sources = service().answer(request.query, limit=request.limit)
    return AnswerResponse(answer=result, sources=source_responses(sources))


@app.post("/insights", response_model=AnswerResponse)
def insights(request: Query):
    result, sources = service().answer(request.query, insight=True, limit=request.limit)
    return AnswerResponse(answer=result, sources=source_responses(sources))
