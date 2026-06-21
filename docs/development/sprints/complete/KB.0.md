# Sprint KB.0 — Project Scaffold

## Scope

Establish the complete project skeleton: all source module files, both database schemas with migration infrastructure, the two-tier config loader, the pipeline DAG, the progress and cancellation abstractions, a runnable FastAPI server shell, a functional Typer CLI entry point, the full test fixture inventory, and the project root bootstrap files. No pipeline stage logic is implemented. Every file created is complete for its KB.0 role — no stubs, no TODOs.

## Builds on

Nothing. This is the first sprint.

## Prerequisites

- Design documents confirmed complete: `VISION.md`, `SPEC.md`, `ARCHITECTURE.md`, `docs/development/TESTING.md`
- `python -m pytest tests/ -q` on a clean checkout produces no failures (vacuously true — no tests exist yet)

---

## Deliverables

### Project root

| File | Notes |
|---|---|
| `run.bat` | (1) create `.venv` if absent; (2) `pip install -r requirements.txt`; (3) copy `config.example.yaml` → `config.yaml` if absent; (4) start uvicorn; (5) open browser to `http://localhost:7700` |
| `requirements.txt` | Pinned. KB.0 packages: `fastapi`, `uvicorn[standard]`, `typer`, `pyyaml`. Dev: `pytest`, `pytest-cov`, `ruff`, `pillow`. Later sprints add to this file in their scope. |
| `config.example.yaml` | Fully annotated template — every key documented with its default and a comment. Matches the annotated example in SPEC.md §Two-Tier Config exactly. Committed to git. |
| `.gitignore` | Add: `config.yaml`, `registry.db`, `knowledge-bases/`, `tmp/`, `.venv/`, `__pycache__/`, `*.pyc`, `*.db`, `tools/exiftool/exiftool.exe`, `tools/ffmpeg/`, `tools/models/**/*.gguf`, `tests/fixtures/corpus/` |

### `src/config.py`

Frozen dataclass. Loads from `config.yaml` (global) and an optional per-KB `config.yaml`. Two-tier merge: per-KB wins on any key it specifies; absent keys inherit from global; invalid values fall back to the next tier with a logged warning.

Key partition (from SPEC.md §Two-Tier Config):
- **Global-only**: `server.host`, `server.port`, `tools.exiftool`, `tools.ffmpeg`, `tools.ffprobe`, `workers.default`
- **Per-KB-only**: `sources`, `focus`, `exiftool_config`
- **Both (per-KB overrides)**: `models.*`, `write_back.include_synonyms`, `thresholds.*`, `workers.count`

Built-in defaults cover every key so the tool starts without any config file.

```python
@dataclass(frozen=True)
class Config:
    # server
    host: str = "127.0.0.1"
    port: int = 7700
    # tools
    exiftool: str = "tools/exiftool/exiftool.exe"
    ffmpeg: str = "tools/ffmpeg/ffmpeg.exe"
    ffprobe: str = "tools/ffmpeg/ffprobe.exe"
    # workers
    workers: int = 4
    # thresholds
    npmi_min_weight: float = 0.1
    suggest_min_files: int = 3
    phash_threshold: int = 10
    describe_frames: int = 9
    scene_threshold: float = 0.4
    deep_seek: bool = True
    deep_seek_max_iter: int = 2
    # write-back
    include_synonyms: bool = False
    confirm_above: int = 200
    writeback_fields: tuple = ("IPTC:Keywords", "XMP:Subject", "XMP:Description")
    # models
    vision_model: str = ""
    vision_gpu_layers: int = -1
    text_model: str = ""
    text_gpu_layers: int = -1
    audio_model: str = ""
    audio_gpu_layers: int = -1
    # per-KB only
    sources: tuple = ()
    focus: str = ""
    exiftool_config: str = ""
```

`load_config(global_path, kb_path=None) -> Config` is the public API. Both paths accept `None`; absent files use built-in defaults.

### `src/db/migrations.py`

```python
def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> None:
```

Reads `_migrations` table to find applied IDs. Applies pending `.sql` files in sorted order. On failure: rolls back, raises `RuntimeError` with migration filename in the message. On success: records the stem in `_migrations` and commits.

The `_migrations` table is guaranteed to exist before the first `apply_migrations` call because `0001_init.sql` creates it with `CREATE TABLE IF NOT EXISTS`.

### `src/db/corpus.py`

```python
def open_corpus(path: Path) -> sqlite3.Connection:
```

Opens (or creates) `corpus.db`, applies PRAGMAs, runs `apply_migrations`. Returns the live connection.

PRAGMAs applied to every connection (corpus and knowledge):
```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA cache_size = -32000;
PRAGMA temp_store = MEMORY;
```

### `src/db/kb.py`

```python
def open_kb(path: Path) -> sqlite3.Connection:
```

Same pattern as `open_corpus`. Applies knowledge.db migrations, which include seeding builtin stopwords on first init.

### `src/migrations/corpus/0001_init.sql`

Full corpus schema as specified in SPEC.md §corpus.db. Every table in the spec, including `_migrations`. Foreign key constraints explicit. No data inserted (corpus starts empty).

Tables: `_migrations`, `sources`, `files`, `file_captured_fields`, `file_exif`, `file_metadata_fields`, `file_metadata_keywords`, `file_hashes`, `file_aesthetic`, `descriptions`, `video_frames`, `candidates`, `transcriptions`, `transcript_segments`, `retag_output`, `file_entity_matches`, `writeback_log`, `pipeline_checkpoints`, `analyse_tokens`.

### `src/migrations/knowledge/0001_init.sql`

Full knowledge schema as specified in SPEC.md §knowledge.db. Includes `_migrations`. After schema creation, seeds builtin stopwords:

```sql
INSERT OR IGNORE INTO stoplist (term, scope, source, added_at)
VALUES
  ('a', 'global', 'builtin', datetime('now')),
  ('an', 'global', 'builtin', datetime('now')),
  ('the', 'global', 'builtin', datetime('now')),
  -- ... full English stopword list (100–150 terms)
  ;
```

Builtin stopwords use `INSERT OR IGNORE` so re-running the migration is safe.

Tables: `_migrations`, `vocabulary`, `stoplist`, `corrections`, `capture_rules`, `substitute_rules`, `reject_tokens`, `kb_version`, `ignored_fields`, `entity_table_registry`, `entity_table_links`.

### `src/pipeline/progress.py`

```python
class ProgressReporter(Protocol):
    def update(self, current: int, total: int, message: str = "") -> None: ...
    def done(self) -> None: ...

class NullProgressReporter:
    def update(self, current: int, total: int, message: str = "") -> None: ...
    def done(self) -> None: ...

class SseProgressReporter:
    def __init__(self, stage: str) -> None: ...
    def update(self, current: int, total: int, message: str = "") -> None: ...
    def done(self) -> None: ...

_progress: dict = {}
_progress_lock: threading.Lock = threading.Lock()
```

`SseProgressReporter.update()` acquires `_progress_lock`, writes `{current, total, rate, eta, status}` to `_progress[stage]`. Rate and ETA computed from elapsed time since first `update()` call. `done()` sets `status: "done"`.

### `src/pipeline/cancel.py`

```python
def make_cancel_event() -> threading.Event:
    return threading.Event()
```

One function. No state. Cancel events are created per-job, not shared.

### `src/pipeline/dag.py`

```python
DEPENDENCIES: dict[str, list[str]] = {
    'analyse':        ['ingest'],
    'normalize':      ['analyse'],
    'extract_meta':   ['normalize'],
    'extract_fields': ['extract_meta'],
    'entity_match':   ['extract_fields'],
    'hash':           ['normalize'],
    'describe':       ['hash'],
    'transcribe':     ['hash'],
    'suggest':        ['describe', 'transcribe'],
    'retag':          ['suggest'],
    'writeback':      ['retag'],
    'export':         ['writeback'],
    'aesthetic':      ['ingest'],
}

TOUCHPOINT_BEFORE: dict[str, str] = {
    'normalize': 'normalise_review',
    'retag':     'suggest_review',
    'writeback': 'new_terms_review',
}

TOUCHPOINTS: set[str] = set(TOUCHPOINT_BEFORE.values())
```

`resolve_plan(target: str, completed: set[str]) -> list[str | dict]` — returns an ordered list of steps needed to reach `target` from `completed`. Each step is either a stage name string or a touchpoint dict `{"touchpoint": name}`. Touchpoints appear immediately before the stage they gate. Completed stages are omitted. Raises `ValueError` for unknown stage names.

Behaviour:
- Walks DEPENDENCIES recursively, collects stages not in `completed`
- Inserts a touchpoint step before any stage that has an entry in `TOUCHPOINT_BEFORE`, if that touchpoint is not in `completed`
- Returns steps in dependency order (prerequisites before dependents)

`INVALIDATES` is declared as a data structure (for `--force` semantics in later sprints) but not used by `resolve_plan` in KB.0:

```python
INVALIDATES: dict[str, list[str]] = {
    'describe':  ['suggest', 'retag'],
    'normalize': ['describe', 'suggest', 'retag'],
    'hash':      [],
}
```

### `src/cli/__init__.py`

Entry point for the `enrich` Typer application. Registers sub-apps: `pipeline`, `kb`, `review`, `aesthetic`, `quick`. Adds the top-level `serve` command:

```
enrich serve   → starts uvicorn; prints "KB Builder running at http://<host>:<port>"
```

`serve` loads the global config, starts `uvicorn.run("src.api:app", host=..., port=..., reload=False)`.

### `src/cli/pipeline.py`, `kb.py`, `review.py`, `aesthetic.py`, `quick.py`

Each defines a `Typer()` app registered under its name in `__init__.py`. No commands implemented yet. Commands are added in the sprint that implements the corresponding stage.

### `src/api/__init__.py`

Creates the FastAPI `app`. Mounts all routers. Adds one route at KB.0:

```
GET /health  →  {"status": "ok", "version": "0.0.1"}
```

### `src/api/pipeline.py`, `kb.py`, `review.py`, `vocabulary.py`, `progress.py`, `settings.py`, `sources.py`, `field_map.py`, `aesthetic.py`, `ui.py`

Each creates an `APIRouter()` and is imported by `api/__init__.py`. No routes implemented yet. Routes are added in the sprint that implements the corresponding feature.

### `templates/base.html`

Minimal HTML5 shell: `<!doctype html>`, `<head>` with charset and viewport, empty `<body>`. No content. Extended by page templates added in later sprints.

### `static/js/htmx.min.js`

HTMX library file. Download and commit. No other JS files at KB.0.

### `static/css/main.css`

Empty file. Populated in later sprints.

### `tests/conftest.py`

Full fixture inventory as documented in `docs/development/TESTING.md`. All fixtures defined even if not used by KB.0 tests — later sprints depend on them being present.

```python
@pytest.fixture
def corpus_db(tmp_path): ...   # opens corpus.db via open_corpus(); returns Connection

@pytest.fixture
def kb_db(tmp_path): ...       # opens knowledge.db via open_kb(); returns Connection

@pytest.fixture
def dbs(tmp_path): ...         # returns (corpus_conn, kb_conn, corpus_path, kb_path)

@pytest.fixture
def null_progress(): ...       # NullProgressReporter()

@pytest.fixture
def no_cancel(): ...           # threading.Event(), never set

@pytest.fixture
def sample_image(tmp_path): ...    # single minimal JPEG via PIL

@pytest.fixture
def sample_images(tmp_path): ...   # five minimal JPEGs via PIL

@pytest.fixture
def sample_video(tmp_path): ...    # 2-second MP4 via ffmpeg testsrc
                                   # skipped automatically if ffmpeg not found at configured path
```

The `sample_video` fixture checks for ffmpeg at the path from `config.example.yaml` defaults. If absent, `pytest.skip("ffmpeg not found")` — do not fail the suite.

---

## Acceptance criteria

All must be true before this sprint is declared complete.

1. `python -m pytest tests/ -q` passes. Count equals or exceeds **25 tests**.
2. `ruff check src/ tests/` passes with zero errors.
3. `open_corpus(tmp_path / "corpus.db")` creates a database containing all 19 tables specified in SPEC.md §corpus.db. A second call on the same path runs without error and produces no duplicate rows.
4. `open_kb(tmp_path / "knowledge.db")` creates a database containing all 11 tables specified in SPEC.md §knowledge.db. The `stoplist` table has at least 50 rows with `source='builtin'` immediately after creation.
5. `apply_migrations` called twice on the same database runs without error. The `_migrations` table contains exactly one row per migration file.
6. `apply_migrations` with a migration file containing invalid SQL raises `RuntimeError` whose message includes the migration filename. The database is not left in a partial-migration state.
7. `load_config(global_path)` with no per-KB path returns a `Config` with all built-in defaults. Attempting to assign any attribute raises `FrozenInstanceError`.
8. `load_config(global_path, kb_path)` where the per-KB config sets `thresholds.suggest_min_files: 5` returns a `Config` where `suggest_min_files == 5` and all other fields match the global config.
9. `resolve_plan('normalize', completed=set())` returns a step list that includes `ingest`, `analyse`, the `normalise_review` touchpoint, and `normalize` — in that order.
10. `resolve_plan('normalize', completed={'ingest', 'analyse'})` returns only the `normalise_review` touchpoint and `normalize`.
11. `resolve_plan('unknown_stage', completed=set())` raises `ValueError`.
12. `enrich --help` runs without error and lists `pipeline`, `kb`, `review`, `aesthetic`, `quick`, and `serve` in the output.
13. The FastAPI `app` responds to `GET /health` with status 200 and body `{"status": "ok"}` (verified via `TestClient`, not a live server).
14. `NullProgressReporter` and `SseProgressReporter` both satisfy the `ProgressReporter` protocol at runtime (verified with `isinstance` against `runtime_checkable` Protocol).

---

## Test targets

### `tests/unit/test_config.py` — 7 tests

| Test | Verifies |
|---|---|
| `test_defaults_apply_when_no_config_files` | All built-in defaults present when no YAML files exist |
| `test_per_kb_overrides_global_for_shared_key` | `suggest_min_files` in per-KB overrides global value |
| `test_absent_per_kb_key_inherits_global` | Key present in global but absent in per-KB → global value used |
| `test_global_only_key_not_settable_per_kb` | `server.port` in per-KB config is ignored (or warned) |
| `test_per_kb_only_key_not_in_global` | `focus` can only be set in per-KB config |
| `test_frozen_raises_on_mutation` | `config.workers = 8` raises `FrozenInstanceError` |
| `test_invalid_value_in_per_kb_falls_back_to_global` | Non-integer where int expected → global value used, warning logged |

### `tests/unit/test_migrations.py` — 5 tests

| Test | Verifies |
|---|---|
| `test_migrations_table_created_by_first_migration` | `_migrations` table exists after `apply_migrations` |
| `test_migration_applied_only_once` | Calling `apply_migrations` twice yields one row in `_migrations` per file |
| `test_pending_migration_applied_after_partial_run` | Simulate partial state; second call applies only the remaining migration |
| `test_failed_migration_rolls_back` | Bad SQL file → `RuntimeError`; no partial table created |
| `test_error_message_includes_filename` | `RuntimeError` message contains the offending `.sql` filename stem |

### `tests/unit/test_dag.py` — 6 tests

| Test | Verifies |
|---|---|
| `test_ingest_plan_is_single_step` | `resolve_plan('ingest', set())` returns `['ingest']` |
| `test_analyse_plan_includes_ingest` | `resolve_plan('analyse', set())` returns `['ingest', 'analyse']` |
| `test_touchpoint_inserted_before_normalize` | `resolve_plan('normalize', set())` contains `normalise_review` touchpoint before `normalize` |
| `test_completed_stages_skipped` | Already-completed stages absent from plan |
| `test_suggest_plan_includes_full_chain` | Plan from empty completed set reaches all prerequisites through `hash`, `describe`, `transcribe` |
| `test_unknown_stage_raises_value_error` | `resolve_plan('nonexistent', set())` raises `ValueError` |

### `tests/integration/test_schema.py` — 7 tests

| Test | Verifies |
|---|---|
| `test_corpus_all_tables_present` | All 19 corpus tables exist after `open_corpus` |
| `test_corpus_wal_mode_enabled` | `PRAGMA journal_mode` returns `'wal'` |
| `test_corpus_foreign_keys_enforced` | INSERT to `files` with non-existent `source_id` raises `IntegrityError` |
| `test_kb_all_tables_present` | All 11 knowledge tables exist after `open_kb` |
| `test_kb_builtin_stopwords_seeded` | `SELECT COUNT(*) FROM stoplist WHERE source='builtin'` returns ≥ 50 |
| `test_kb_foreign_keys_enforced` | INSERT to `entity_table_links` with non-existent `parent_table` raises `IntegrityError` |
| `test_migrations_idempotent_on_reopen` | Close and reopen existing DB; `apply_migrations` runs without error; row counts unchanged |

**Total: 25 tests**

---

## Out of scope

- Any pipeline stage function (`ingest.py`, `analyse.py`, etc.) — KB.1+
- `exiftool.py` wrapper — KB.1 (first used by Extract Metadata)
- `registry.db` management and `enrich kb` commands — KB.7
- Seed data files (`seed/stopwords.txt`, `seed/general-media/`) — KB.7 (loaded by `enrich kb create`)
- All review queue templates — added in the sprint that implements the corresponding review
- `enrich serve` opening a browser directly from Python — `run.bat` handles that; the Python command only starts uvicorn
- Level C (LLM) suggest — KB.5
- Health checker UI component — KB.1

---

## Notes

**`0001_init.sql` scope:** Both migration files contain the complete current schema as of this sprint. Future schema changes go in `0002_...sql`, etc. Do not modify `0001_init.sql` after this sprint ships — that would require re-applying migrations on existing databases.

**Builtin stopwords list:** Use NLTK's standard English stopword corpus as the source (roughly 150 terms). The list is hardcoded into `0001_init.sql` as INSERT statements — it does not depend on NLTK at runtime. This is a one-time authoring step during sprint implementation.

**`resolve_plan` touchpoint representation:** Touchpoints in the returned list are represented as dicts `{"touchpoint": "normalise_review"}` to distinguish them from runnable stage name strings. This is the type that the UI pipeline planner and `enrich run` both consume — keep it consistent.

**FastAPI test approach:** Acceptance criterion 13 uses `fastapi.testclient.TestClient` — no live server, no port binding. This is the correct pattern for all API integration tests throughout the project.

**`sample_video` fixture skip condition:** Check for ffmpeg using `shutil.which` against the default path from config, not just PATH. If absent, call `pytest.skip()` inside the fixture body so tests that don't use it are unaffected.
