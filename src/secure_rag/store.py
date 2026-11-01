import json
import os
import tempfile
from pathlib import Path

from .security import ID_PATTERN, Post


class PostStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, post_id: str) -> Path:
        if not ID_PATTERN.fullmatch(post_id):
            raise ValueError("invalid post id")
        return self.root / f"{post_id}.json"

    def get(self, post_id: str) -> Post | None:
        path = self.path(post_id)
        if not path.exists():
            return None
        return Post.model_validate_json(path.read_text(encoding="utf-8"))

    def put(self, post: Post) -> None:
        path = self.path(post.id)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, delete=False) as file:
            json.dump(post.model_dump(), file, ensure_ascii=False)
            temporary = Path(file.name)
        os.replace(temporary, path)

    def delete(self, post_id: str) -> bool:
        path = self.path(post_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def all(self) -> list[Post]:
        return [Post.model_validate_json(path.read_text(encoding="utf-8")) for path in sorted(self.root.glob("*.json"))]
