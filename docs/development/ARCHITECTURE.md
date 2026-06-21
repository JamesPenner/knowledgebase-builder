# Architecture

## Module Layout

```
src/
  config.py             Config frozen dataclass; two-tier merge (global → per-KB); validation at load
  exiftool.py           ExifTool wrapper — stay_open persistent process, batch calls
  cli/                  Typer CLI package (thin I/O layer only)
    __init__.py         registers sub-apps; entry point for `enrich` command
    pipeline.py         ingest, analyse, normalize, extract, hash, describe, transcribe,
                        suggest, retag, writeback, export, run
    kb.py               kb create, kb list, kb delete, kb set-active
    review.py           review normalise, review suggest, review new-terms
    aesthetic.py        aesthetic (--writeback, --export, --force)
    quick.py            quick-describe, quick-transcribe — stateless, no KB required
  api/                  FastAPI package (thin I/O layer only)
    __init__.py         creates app; mounts all routers
    pipeline.py         /api/stages/* routes
    kb.py               /api/kb/* routes
    review.py           /api/review/* routes
    vocabulary.py       /api/vocabulary/* routes
    progress.py         /api/progress/* SSE routes
    settings.py         /api/settings/* routes
    sources.py          /api/sources/* routes
    field_map.py        /api/field-map/* routes
    aesthetic.py        /api/aesthetic/* routes
    ui.py               page routes
  stages/               one module per pipeline stage; each independently invokable
    ingest.py           Stage 0
    analyse.py          Stage 0.5
    normalize.py        Stage 1
    extract_meta.py     Stage 1.5
    extract_fields.py   Stage 1.6
    field_registry.py   built-in default_fields registry; corpus-aware field_map.csv generation
    entity_match.py     Stage 1.7
    hash.py             Stage 2
    describe.py         Stage 3a
    transcribe.py       Stage 3b
    video.py            frame pipeline for video describe
    aesthetic.py        optional scoring (NIMA + CLIP)
    suggest.py          Stage 4
    retag.py            Stage 5
    writeback.py        Stage 6
    sync.py             KB sync — version stamps, selective analysis, dirty set
    export.py           Stage 7
  db/
    corpus.py           corpus.db connection, schema init, named query functions
    kb.py               knowledge.db connection, schema init, CRUD helpers
    migrations.py       shared migration runner (_migrations table approach)
  pipeline/
    dag.py              DEPENDENCIES + INVALIDATES dicts; TOUCHPOINTS set; resolve_plan()
    progress.py         SseProgressReporter + NullProgressReporter; _progress dict + lock
    cancel.py           threading.Event factory; cooperative cancellation
  migrations/
    corpus/             numbered SQL files for corpus.db evolution
    knowledge/          numbered SQL files for knowledge.db evolution
```

## Design Principles

### Thin edges, thick core

`src/cli/` and `src/api/` are pure I/O layers — argument parsing and HTTP routing respectively. All business logic lives in `src/stages/`. Both surfaces call the same underlying functions. No pipeline logic in either entry point.

### Stage functions take explicit dependencies

Every stage function follows this signature:

```python
def run_ingest(corpus_path, kb_path, config, progress, cancel_event):
    ...
```

No module-level globals imported from siblings. Config, progress reporter, and cancel event are passed in. Stages are independently testable with `tmp_path` SQLite databases — no mock patching required.

### DB access through named functions, not inline SQL

`db/corpus.py` exposes `get_pending_files(db)`, `mark_described(db, file_id)`, etc. Stage modules never write raw SQL inline. When a query changes, it changes in one place.

### Config is a frozen dataclass

```python
@dataclass(frozen=True)
class Config:
    describe_frames: int = 9
    phash_threshold: int = 10
    workers: int = ...
```

Loaded once at startup, passed down the call stack. Never mutated at runtime.

### Progress is injectable via protocol

```python
class ProgressReporter(Protocol):
    def update(self, current: int, total: int, message: str = "") -> None: ...
    def done(self) -> None: ...
```

`SseProgressReporter` updates `_progress` dict and triggers SSE. `NullProgressReporter` is the no-op for tests and `--quiet` CLI runs. Stages have no import-time dependency on the web layer. The `_progress` dict is protected by `threading.Lock()`.

### The DAG is data, not code

Stage dependencies are declared as a plain dict in `pipeline/dag.py`. `resolve_plan(target_stage, completed_stages, touchpoints)` returns a list of `PlanStep` (union of `RunnableStage` and `ReviewTouchpoint`). The UI pipeline planner and `enrich run` both call this — single source of truth.

```python
DEPENDENCIES = {
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

TOUCHPOINTS = {'normalise_review', 'suggest_review', 'new_terms_review'}
```

### Async boundary — BackgroundTasks, not asyncio.to_thread

Stage-launching endpoints use FastAPI's `BackgroundTasks`. The endpoint returns `{"job_id": ..., "status": "started"}` immediately; the stage runs after the response is sent. `asyncio.to_thread()` is only for short awaitable DB calls from async endpoints, never for stage dispatch.

### Cooperative cancellation

Every long-running stage accepts `cancel_event: threading.Event`. Workers check `cancel_event.is_set()` at the top of each item loop iteration. `POST /api/stages/{stage}/cancel` sets the event; the worker exits cleanly after the current item completes.

## API Patterns

All endpoints follow one of three shapes. New endpoints instantiate a pattern — they do not invent new shapes.

### Pattern 1 — Stage control

```
POST   /api/stages/{stage}/run       → {"job_id": str, "status": "started"}
POST   /api/stages/{stage}/cancel    → {"status": "cancelled"}
GET    /api/stages/{stage}/status    → {"status": str, "current": int, "total": int,
                                        "rate": float, "eta": int}
GET    /api/stages/{stage}/stream    → text/event-stream; emits status objects;
                                        sends current state immediately on connect
```

`{stage}` is the DAG key (e.g. `ingest`, `describe`, `suggest`).

### Pattern 2 — Review queue

```
GET    /api/review/{queue}/pending          → {"items": [...], "total": int, "reviewed": int}
POST   /api/review/{queue}/decide           → {"item_id": int, "action": str, "value"?: str}
GET    /api/review/{queue}/decisions        → {"decisions": [...]}
DELETE /api/review/{queue}/decisions/{id}   → {}
```

`{queue}` values: `normalise`, `suggest`, `new-terms`.

### Pattern 3 — KB management

```
GET    /api/kb                   → {"kbs": [...]}
POST   /api/kb                   → {"name": str, "template": str, "seed_path"?: str}
DELETE /api/kb/{name}            → {}
POST   /api/kb/{name}/activate   → {}
GET    /api/kb/{name}/health     → {"checks": [...]}
```

## SQLite Conventions

### Connection model

Each worker thread in `ThreadPoolExecutor` opens its own `sqlite3.connect()`. Connection objects must not cross thread boundaries. Every connection opens with:

```python
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA cache_size = -32000;
PRAGMA temp_store = MEMORY;
```

`foreign_keys = ON` is required — the schema has explicit FK references that are silently unenforced without it.

### Migration tracking

Both databases use a `_migrations` table (not `PRAGMA user_version`). Each migration SQL file is applied once; the filename stem is recorded as the applied ID. Safe for out-of-order application. A failed migration rolls back cleanly and surfaces a clear error.

```
src/migrations/
  corpus/
    0001_init.sql
    0002_...
  knowledge/
    0001_init.sql
    0002_...
```

### Cross-database queries

`sync.py` and `retag.py` read from both databases simultaneously using SQLite `ATTACH`:

```python
conn = sqlite3.connect(corpus_path)
conn.execute("ATTACH DATABASE ? AS knowledge", (str(kb_path),))
# now access knowledge.vocabulary, knowledge.kb_version, etc.
```

The `ATTACH` must remain active for the connection's lifetime. Do not close and re-open between cross-DB queries.

## Import and Dependency Discipline

### Lazy imports for heavy dependencies

ML and NLP libraries are imported inside the function that needs them, never at module level:

```python
# Good
def run_suggest(...):
    import spacy
    nlp = spacy.load("en_core_web_sm")

# Bad — loads spaCy every time suggest.py is imported
import spacy
```

Apply to: `llama_cpp`, `spacy`, `networkx`, `community`, `PIL`, all ONNX-related imports.

### No circular imports

One-way dependency graph only:
- `api/` and `cli/` import from `stages/`, `db/`, `pipeline/`
- `stages/` imports from `db/` and `pipeline/`
- Nothing imports from `api/` or `cli/`
- Stage modules do not import from each other

### No wildcard imports

`from module import *` is never used. Ruff enforces this as a build failure.

## Frontend

HTMX + vanilla JS + handwritten CSS only. No jQuery, React, Vue, Bootstrap, or Tailwind. Each JS file serves one purpose; CSS is scoped to the component it styles. A new contributor should be able to read any template or script file without framework knowledge.

## File Conventions

- All temporary artefacts go in `tmp/` (gitignored)
- All documentation goes in `docs/`
- No documentation outside `docs/` except the root `README.md` and `CLAUDE.md`
- KB data (databases, exports) lives under `knowledge-bases/<name>/` (gitignored)
- Committed binaries: none — tool executables go in `tools/` (gitignored); models are user-provided
