# Testing Guide

## Running the suite

```
python -m pytest tests/ -q
ruff check src/ tests/
```

Both must pass. Ruff is not advisory — import errors and unused names are build failures equivalent to a failing test. Run them together before declaring any sprint done.

Coverage report (optional, for sprint verification):

```
python -m pytest tests/ -q --cov=src --cov-report=term-missing
```

---

## Directory layout

```
tests/
  conftest.py              shared fixtures: tmp corpus.db + knowledge.db, synthetic media helpers
  unit/                    pure function tests — no DB, no filesystem
  integration/             stage tests — real SQLite in tmp_path, real ExifTool where required
  fixtures/
    corpus/                gitignored — place real files here for manual spot-checking only
    seeds/                 SQL files that seed knowledge.db state for integration tests
```

Tests never live in `src/`. Never place test files alongside source modules.

---

## `conftest.py` — shared fixtures

`conftest.py` provides the fixtures every stage test needs. The canonical set:

```python
@pytest.fixture
def corpus_db(tmp_path):
    """Fresh corpus.db with schema applied. Returns sqlite3.Connection."""
    from db.corpus import open_corpus
    return open_corpus(tmp_path / "corpus.db")

@pytest.fixture
def kb_db(tmp_path):
    """Fresh knowledge.db with schema applied. Returns sqlite3.Connection."""
    from db.kb import open_kb
    return open_kb(tmp_path / "knowledge.db")

@pytest.fixture
def dbs(tmp_path):
    """Both databases. Returns (corpus_conn, kb_conn, corpus_path, kb_path)."""
    from db.corpus import open_corpus
    from db.kb import open_kb
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    return open_corpus(corpus_path), open_kb(kb_path), corpus_path, kb_path

@pytest.fixture
def null_progress():
    from pipeline.progress import NullProgressReporter
    return NullProgressReporter()

@pytest.fixture
def no_cancel():
    import threading
    return threading.Event()   # never set — cancellation never fires

@pytest.fixture
def sample_image(tmp_path):
    """Minimal valid JPEG in tmp_path."""
    from PIL import Image
    path = tmp_path / "test_image.jpg"
    Image.new("RGB", (64, 64), color=(128, 64, 32)).save(path, "JPEG")
    return path

@pytest.fixture
def sample_images(tmp_path):
    """Five minimal valid JPEGs with distinct filenames."""
    from PIL import Image
    paths = []
    for i in range(5):
        p = tmp_path / f"img_{i:03d}.jpg"
        Image.new("RGB", (64, 64), color=(i * 40, 100, 200)).save(p, "JPEG")
        paths.append(p)
    return paths

@pytest.fixture
def sample_video(tmp_path):
    """Short valid MP4 via ffmpeg testsrc."""
    import subprocess
    path = tmp_path / "test_video.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=64x64:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)
    ], check=True, capture_output=True)
    return path
```

Add new shared fixtures to `conftest.py` only when multiple test modules need them. Fixtures used by a single test module stay in that module.

---

## Unit tests (`tests/unit/`)

Unit tests cover individual functions in isolation. **No DB connection. No filesystem access. No subprocess calls.**

When to write a unit test:
- Pure logic: regex parsers, format_str evaluation, NPMI arithmetic, config merging
- Functions whose correctness can be verified with in-memory inputs and deterministic outputs
- Edge cases that would be expensive to trigger through a full integration run

Isolation pattern:

```python
# tests/unit/test_format_str_parser.py

from stages.normalize import apply_format_str

def test_basic_slice():
    assert apply_format_str("{1:0:4}-{1:4:6}-{1:6:8}", ["160929"]) == "16-09-29"

def test_literal_passthrough():
    assert apply_format_str("20{1}", ["241115"]) == "20241115"

def test_full_match_group_zero():
    assert apply_format_str("{0}", ["abc"]) == "abc"
```

No mocking of DB helpers. If a function requires a DB connection to do its work, it belongs in an integration test, not a unit test. Redesign the function's interface if needed.

---

## Integration tests (`tests/integration/`)

Integration tests run against real SQLite databases in `tmp_path`. No mocking of DB connections, ExifTool, or ffmpeg. The database state after a stage run is the test assertion target.

**Every stage integration test must cover three cases:**

1. **Happy path** — valid inputs, stage runs to completion, corpus.db state is correct
2. **Resume on restart** — simulate an interrupted run (process subset of files, verify status), re-run stage, verify only remaining files were processed and final state is identical to a clean full run
3. **At least one failure mode** — corrupted input file, missing prerequisite row, invalid rule, ExifTool error; verify the stage records a failure status and does not crash

Pattern:

```python
# tests/integration/test_ingest.py

from stages.ingest import run_ingest

def test_ingest_happy_path(dbs, sample_images, null_progress, no_cancel, tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = dbs
    source_dir = sample_images[0].parent

    run_ingest(corpus_path, kb_path, config_with_source(source_dir), null_progress, no_cancel)

    rows = corpus_conn.execute("SELECT path FROM files").fetchall()
    assert len(rows) == len(sample_images)

def test_ingest_resume(dbs, sample_images, null_progress, no_cancel, tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = dbs
    source_dir = sample_images[0].parent
    cfg = config_with_source(source_dir)

    # First run — interrupt after first file by checking state mid-way is irrelevant;
    # simulate by manually inserting only 2 of 5 files with same stat values
    # then run ingest and verify only 3 new rows are inserted
    ...

def test_ingest_skips_unchanged_files(dbs, sample_images, null_progress, no_cancel):
    ...
    # Run twice; second run should add 0 new rows (same path+size+mtime)
    assert second_count == first_count
```

Stage functions always receive `corpus_path` and `kb_path` as `pathlib.Path` objects, not open connections. The stage opens its own connection. This is how they are called in production; tests must call them the same way.

---

## Synthetic media generation

The test suite generates its own media files in `tmp_path`. No real files are committed to git. No test should depend on a file in `tests/fixtures/corpus/`.

**Images** — PIL generates minimal valid JPEGs and PNGs:
```python
from PIL import Image
Image.new("RGB", (64, 64), color=(128, 64, 32)).save(path, "JPEG")
```

**Videos** — ffmpeg's `testsrc` filter produces valid MP4s:
```python
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
    "testsrc=duration=2:size=64x64:rate=10",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
    check=True, capture_output=True)
```

**Metadata injection** — ExifTool writes test metadata onto generated files:
```python
subprocess.run(["exiftool", "-overwrite_original",
    "-XMP-dc:Description=Test description",
    "-IPTC:Keywords=bridge", "-IPTC:Keywords=highway",
    str(path)], check=True, capture_output=True)
```

Keep synthetic files small (64×64 pixels, 2-second videos). Test correctness, not throughput.

---

## Seed fixtures (`tests/fixtures/seeds/`)

SQL files that populate `knowledge.db` with known state for integration tests that depend on an existing KB (normalize, entity_match, sync, writeback). Each seed file is self-contained:

```sql
-- tests/fixtures/seeds/basic_corrections.sql
INSERT INTO corrections (raw_term, canonical_term, type, correction_kind, added_at)
VALUES ('TuckInleted', 'Tuck Inlet', 'exact', 'typo', datetime('now'));
```

Load in a fixture or test setup:

```python
kb_conn.executescript(Path("tests/fixtures/seeds/basic_corrections.sql").read_text())
```

Seed files use `INSERT OR IGNORE` to be safe for repeated application.

---

## Coverage by component

| Component | Test type | Required coverage |
|---|---|---|
| `normalize.py` | Unit | Processing order (reject→capture→substitute→correct), format_str parser (slices, literals, group 0), UPSERT semantics on re-run, each instrument in isolation |
| `suggest.py` Levels A+B | Unit | NPMI computation against known fixture term sets, Louvain cluster detection, memory-safe streaming accumulator |
| `config.py` | Unit | Two-tier merge (per-KB overrides global), invalid values fall back correctly, frozen dataclass raises on mutation |
| `dag.py` | Unit | `resolve_plan()` produces correct step sequence for each target stage, touchpoints appear at correct positions, circular dependency raises |
| `ingest.py` | Integration | Happy path, resume (skip unchanged files), new source added to existing corpus |
| `analyse.py` | Integration | Token grouping, pattern classification, `analyse_tokens` populated correctly, common prefix stripping |
| `extract_meta.py` | Integration | ExifTool JSON stored in `file_exif`, re-run skips already-extracted files, `--force` re-extracts |
| `extract_fields.py` | Integration | Scalar fields → `file_metadata_fields`, keyword fields → `file_metadata_keywords`, field_map.csv applied, staleness on field_map change |
| `hash.py` | Integration | SHA-256 + pHash stored, duplicate detected (`canonical_id` set), re-run with `--force` rebuilds relationships |
| `entity_match.py` | Integration | Two-step trigger (word boundary → key column), linked table traversal, cycle detection at max depth, GPS match |
| `sync.py` | Integration | KB version increments on mutation, dirty-set computation, all `change_type` paths for selective analysis |
| `writeback.py` | Integration | ExifTool writes XMP fields to file, `writeback_log` records outcome, partial failure recorded per-file not per-batch |
| `migrations.py` | Unit | Applied migrations not re-applied, failed migration rolls back cleanly, `_migrations` table created by 0001_init |
| `describe.py`, `video.py` | Manual only | GPU/LLM — not run in CI |
| `transcribe.py` | Manual only | Whisper — not run in CI |
| `retag.py` | Manual only | Text LLM — not run in CI |
| `aesthetic.py` | Manual only | ONNX model dependency — not run in CI |

---

## Definition of done per stage

A stage is **test-complete** when all three of the following pass:

1. **Happy path test passes** against the fixture corpus in `tmp_path`
2. **Resume-on-restart test passes**: interrupt after partial processing, re-run, final corpus.db state is identical to a clean full run, no duplicate rows
3. **At least one failure mode is tested**: the stage records a failure status (not exception), the database is not left in an inconsistent state, and re-running the stage recovers cleanly

A stage that meets the definition of done for a previous sprint does not need to be re-verified unless the sprint's scope modifies that stage.

---

## GPU/LLM stages — manual validation only

Stages 3a (Describe), 3b (Transcribe), and 5 (Retag) are excluded from the automated test suite. They depend on hardware (GPU/VRAM) and non-deterministic model output that makes automated assertion impractical.

Manual validation checklist for each:
- Stage runs to completion on a small fixture corpus (5–10 files)
- Resume works: kill mid-run, re-run, only remaining files processed
- `--force` re-runs all files
- `ModelLoadError` surfaces correctly when model path is wrong
- Progress SSE stream emits updates and terminates with `status: done`

Document manual validation results in the sprint memory entry.

---

## What ruff checks

`ruff check src/ tests/` enforces:
- No unused imports (`F401`)
- No wildcard imports (`F403`)
- No undefined names (`F821`)
- No unused variables (`F841`)

These are treated as build failures. Fix before committing. Ruff does not check style (line length, formatting) — only import hygiene and obvious errors.
