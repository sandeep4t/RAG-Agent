from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")

    write_key: str = ""
    qdrant_url: str = "http://127.0.0.1:6333"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    posts_dir: Path = Path("data/posts")
    audit_db: Path = Path("data/audit/events.sqlite3")
    allowed_authors: str = "demo-editor"
    collection: str = "secure_posts_v1"

    @property
    def authors(self) -> set[str]:
        return {author.strip() for author in self.allowed_authors.split(",") if author.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
