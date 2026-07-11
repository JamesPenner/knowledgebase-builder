"""Integration tests for the corpus file browser — JSON API, page, and HTMX partial (KB.AK1)."""
import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import add_source, open_corpus, upsert_file
from src.db.kb import open_kb


def _open_dbs(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()
    return corpus_path, kb_path


def _make_client(corpus_path, kb_path):
    def _override():
        return corpus_path, kb_path
    app.dependency_overrides[resolve_kb] = _override
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(resolve_kb, None)


def _patch_registry(monkeypatch, tmp_path):
    monkeypatch.setattr("src.db.registry.get_kb_path", lambda reg, name: tmp_path)
    monkeypatch.setattr("src.db.registry.open_registry", lambda p: None)


def _seed(conn, n: int = 3):
    src_id = add_source(conn, "/test")
    fids = []
    for i in range(n):
        fid = upsert_file(conn, src_id, f"/test/f{i}.jpg", f"f{i}.jpg", ".jpg", "images", 1000, 0.0)
        fids.append(fid)
    conn.commit()
    return src_id, fids


# ---------------------------------------------------------------------------
# GET /api/kb/{name}/files
# ---------------------------------------------------------------------------

def test_api_files_empty(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/files")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


def test_api_files_lists_and_counts(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _seed(conn, 3)
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/files")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert body["items"][0]["path"] == "/test/f0.jpg"


def test_api_files_pagination(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _seed(conn, 5)
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/files?limit=2&offset=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert [f["path"] for f in body["items"]] == ["/test/f2.jpg", "/test/f3.jpg"]


def test_api_files_filters_by_file_type(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src_id = add_source(conn, "/test")
    upsert_file(conn, src_id, "/test/a.jpg", "a.jpg", ".jpg", "images", 1000, 0.0)
    upsert_file(conn, src_id, "/test/b.mp4", "b.mp4", ".mp4", "video", 1000, 0.0)
    conn.commit()
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/files?file_type=video")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["path"] == "/test/b.mp4"


def test_api_files_state_filter(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src_id = add_source(conn, "/test")
    fid = upsert_file(conn, src_id, "/test/a.jpg", "a.jpg", ".jpg", "images", 1000, 0.0)
    conn.execute("INSERT INTO descriptions (file_id, pass1_status) VALUES (?, 'done')", (fid,))
    upsert_file(conn, src_id, "/test/b.jpg", "b.jpg", ".jpg", "images", 1000, 0.0)
    conn.commit()
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/files?state=described")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["path"] == "/test/a.jpg"


def test_api_files_sort_by_file_size_desc(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src_id = add_source(conn, "/test")
    upsert_file(conn, src_id, "/test/small.jpg", "small.jpg", ".jpg", "images", 10, 0.0)
    upsert_file(conn, src_id, "/test/big.jpg", "big.jpg", ".jpg", "images", 1000, 0.0)
    conn.commit()
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/files?sort_by=file_size&sort_order=desc")
    assert resp.status_code == 200
    body = resp.json()
    assert [f["path"] for f in body["items"]] == ["/test/big.jpg", "/test/small.jpg"]


# ---------------------------------------------------------------------------
# GET /corpus-files (page)
# ---------------------------------------------------------------------------

def test_corpus_files_page_loads(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/corpus-files?kb=test")
    assert resp.status_code == 200
    assert b"Corpus Files" in resp.content


# ---------------------------------------------------------------------------
# GET /corpus-files/partials/list (HTMX partial)
# ---------------------------------------------------------------------------

def test_corpus_files_partial_renders_rows(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    _seed(conn, 3)
    conn.close()
    open_kb(kb_path).close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/corpus-files/partials/list?kb=test")
    assert resp.status_code == 200
    assert b"f0.jpg" in resp.content
    assert b"f1.jpg" in resp.content


def test_corpus_files_partial_load_more(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    _seed(conn, 3)
    conn.close()
    open_kb(kb_path).close()
    client = _make_client(corpus_path, kb_path)

    resp = client.get("/corpus-files/partials/list?kb=test&limit=2")
    assert resp.status_code == 200
    assert b"data-load-more" in resp.content

    resp_full = client.get("/corpus-files/partials/list?kb=test&limit=3")
    assert resp_full.status_code == 200
    assert b"data-load-more" not in resp_full.content


def test_corpus_files_partial_repopulates_source_dropdown(tmp_path):
    """Regression: the partial re-renders the whole filter bar, so it must
    receive `sources` on every request, not just on the initial page load."""
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    _seed(conn, 1)
    conn.close()
    open_kb(kb_path).close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/corpus-files/partials/list?kb=test")
    assert resp.status_code == 200
    assert b'<option value="1"' in resp.content
    assert b"/test" in resp.content


def test_corpus_files_partial_empty_state(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/corpus-files/partials/list?kb=test")
    assert resp.status_code == 200
    assert b"No files match" in resp.content


# ---------------------------------------------------------------------------
# Nav link
# ---------------------------------------------------------------------------

def test_files_nav_link_present(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/corpus-files?kb=test")
    assert resp.status_code == 200
    assert b'href="/corpus-files?kb=test"' in resp.content
