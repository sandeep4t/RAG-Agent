import json
from pathlib import Path

from secure_rag.config import get_settings
from secure_rag.rag import RagService
from secure_rag.security import Post, validate_post


def main():
    service = RagService(get_settings())
    path = Path(__file__).resolve().parents[1] / "samples" / "posts.json"
    for item in json.loads(path.read_text(encoding="utf-8")):
        post = Post.model_validate(item)
        validate_post(post, service.settings.authors)
        service.store.put(post)
        service.index(post)
        print(f"Indexed {post.id}")


if __name__ == "__main__":
    main()
