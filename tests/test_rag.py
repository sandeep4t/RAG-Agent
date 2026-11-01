from itertools import pairwise
from types import SimpleNamespace

import secure_rag.rag as rag_module
from secure_rag.config import Settings
from secure_rag.rag import RagService, Source, chunks
from secure_rag.security import Post, digest
from secure_rag.store import PostStore


class Embedder:
    def encode(self, value):
        if isinstance(value, str):
            return SimpleNamespace(tolist=lambda: [0.1, 0.2])
        return SimpleNamespace(tolist=lambda: [[0.1, 0.2] for _ in value])

    def get_sentence_embedding_dimension(self):
        return 2


class Reranker:
    def predict(self, pairs):
        return [float(len(text)) for _, text in pairs]


class Client:
    def __init__(self):
        self.points = []
        self.last_query = None

    def collection_exists(self, name):
        return True

    def delete(self, **kwargs):
        self.points = []

    def upsert(self, **kwargs):
        self.points = kwargs["points"]

    def query_points(self, **kwargs):
        self.last_query = kwargs
        return SimpleNamespace(points=[SimpleNamespace(payload=point.payload, score=0.5)
                                       for point in self.points])


def test_hybrid_retrieval_and_source_integrity(tmp_path):
    store = PostStore(tmp_path)
    client = Client()
    rag = RagService(Settings(posts_dir=tmp_path), store, client, Embedder(), Reranker())
    post = Post(id="post-one", title="Hybrid", author="demo-editor", body="Dense and BM25 search")
    store.put(post)
    rag.index(post)
    assert rag.search("hybrid")[0].post_id == post.id
    assert len(client.last_query["prefetch"]) == 2
    assert client.points[0].payload["digest"] == digest(post)
    store.put(post.model_copy(update={"body": "Changed content"}))
    assert rag.search("hybrid") == []
    store.delete(post.id)
    assert rag.search("hybrid") == []


def test_chunk_overlap():
    post = Post(id="long", title="Title", author="demo-editor", body="x" * 30)
    pieces = chunks(post, size=20, overlap=5)
    assert all(previous[-5:] == following[:5] for previous, following in pairwise(pieces))


def test_answer_retries_uncited_output(monkeypatch, tmp_path):
    rag = RagService(Settings(posts_dir=tmp_path), PostStore(tmp_path), Client(), Embedder(), Reranker())
    source = Source("post-one", "Title", "demo-editor", "Dense and BM25 complement each other.", 1.0)
    monkeypatch.setattr(rag, "search", lambda query, limit=5: [source])

    class FakeHttp:
        calls = 0

        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json):
            self.calls += 1
            answer = "Dense and BM25 complement each other."
            if self.calls == 2:
                answer += " [1]"
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"message": {"content": answer}})

    monkeypatch.setattr(rag_module.httpx, "Client", FakeHttp)
    answer, sources = rag.answer("Why combine them?")
    assert answer.endswith("[1]")
    assert sources == [source]
