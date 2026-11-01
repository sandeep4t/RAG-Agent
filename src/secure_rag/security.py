import hashlib
import json
import re

from pydantic import BaseModel, Field, field_validator

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore (all )?(previous|above|prior) instructions",
        r"(reveal|print|show) (the )?(system|developer) prompt",
        r"(send|exfiltrate|upload) .{0,80}(secret|api.key|password)",
        r"\b(system|developer|assistant)\s*:\s*(ignore|override|reveal)",
    )
]


class Post(BaseModel):
    id: str
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=20_000)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not ID_PATTERN.fullmatch(value):
            raise ValueError("id must contain lowercase letters, digits, and hyphens")
        return value


def validate_post(post: Post, authors: set[str]) -> None:
    if post.author not in authors:
        raise ValueError("author is not allowlisted")
    if any(pattern.search(post.title + "\n" + post.body) for pattern in INJECTION_PATTERNS):
        raise ValueError("post contains a prompt-injection pattern")


def digest(post: Post) -> str:
    canonical = json.dumps(post.model_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
