"""Integration tests for the four-section nav structure (KB.AI1)."""
import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus
from src.db.kb import open_kb


def _make_client(corpus_path, kb_path):
    def _override():
        return corpus_path, kb_path
    app.dependency_overrides[resolve_kb] = _override
    return TestClient(app)


@pytest.fixture()
def nav_client(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()
    client = _make_client(corpus_path, kb_path)
    yield client
    app.dependency_overrides.pop(resolve_kb, None)


# ---------------------------------------------------------------------------
# Section labels
# ---------------------------------------------------------------------------

class TestNavSections:
    def test_build_section_label_present(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert resp.status_code == 200
        assert 'class="nav-label">Build' in resp.text

    def test_review_section_label_present(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'class="nav-label">Review' in resp.text

    def test_knowledge_section_label_present(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'class="nav-label">Knowledge' in resp.text

    def test_corpus_section_label_present(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'class="nav-label">Corpus' in resp.text


# ---------------------------------------------------------------------------
# Build section links
# ---------------------------------------------------------------------------

class TestBuildSection:
    def test_workbench_link_present(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'href="/pipeline?kb=test"' in resp.text

    def test_workbench_link_text(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert ">Workbench<" in resp.text


# ---------------------------------------------------------------------------
# Review section links and badge spans
# ---------------------------------------------------------------------------

class TestReviewSection:
    def test_normalise_link_present(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'href="/review/normalise?kb=test"' in resp.text

    def test_suggest_link_present(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'href="/review/suggest?kb=test"' in resp.text

    def test_new_terms_link_present(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'href="/review/new-terms?kb=test"' in resp.text

    def test_normalise_badge_span_present(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'id="normalise-badge"' in resp.text

    def test_suggest_badge_span_present(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'id="suggest-badge"' in resp.text

    def test_new_terms_badge_span_present(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'id="new-terms-badge"' in resp.text


# ---------------------------------------------------------------------------
# Corpus section links
# ---------------------------------------------------------------------------

class TestCorpusSection:
    def test_stats_link_in_corpus_section(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'href="/corpus-stats?kb=test"' in resp.text

    def test_health_link_in_corpus_section(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'href="/health?kb=test"' in resp.text


# ---------------------------------------------------------------------------
# JS loading
# ---------------------------------------------------------------------------

class TestNavJS:
    def test_nav_badges_js_loaded(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert "nav_badges.js" in resp.text

    def test_suggest_badge_js_not_referenced(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert "suggest_badge.js" not in resp.text

    def test_kb_switcher_has_data_kb_attribute(self, nav_client):
        resp = nav_client.get("/pipeline", params={"kb": "test"})
        assert 'data-kb="test"' in resp.text


# ---------------------------------------------------------------------------
# Nav renders on non-pipeline pages too
# ---------------------------------------------------------------------------

class TestNavOnOtherPages:
    def test_four_sections_on_health_page(self, nav_client):
        resp = nav_client.get("/health", params={"kb": "test"})
        assert resp.status_code == 200
        assert 'class="nav-label">Build' in resp.text
        assert 'class="nav-label">Review' in resp.text
        assert 'class="nav-label">Corpus' in resp.text

    def test_four_sections_on_normalise_page(self, nav_client):
        resp = nav_client.get("/review/normalise", params={"kb": "test"})
        assert resp.status_code == 200
        assert 'class="nav-label">Review' in resp.text
        assert 'href="/review/suggest?kb=test"' in resp.text
