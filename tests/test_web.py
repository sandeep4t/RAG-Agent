from secure_rag.api import STATIC_DIR, chat_page


def test_chat_page_and_assets_are_packaged_together():
    assert chat_page().path == STATIC_DIR / "index.html"
    assert (STATIC_DIR / "index.html").is_file()
    assert (STATIC_DIR / "styles.css").is_file()
    assert (STATIC_DIR / "app.js").is_file()
    assert (STATIC_DIR / "favicon.svg").is_file()
