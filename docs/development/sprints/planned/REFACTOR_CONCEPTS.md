# Architectural Refactor Concepts

Candidate refactors identified after the FrameSet/AudioTrack design session.
None are scheduled sprints. Each is self-contained and can be prioritised
independently. Ordered by estimated payoff.

---

## 1. FileContext — Typed Per-File Text Assembly

### Problem

Every text-consuming stage independently queries the same DB columns per file
and assembles them into a usable representation:

| Stage | What it queries |
|---|---|
| `summarize.py:_assemble_context()` | description, transcript segments, tags, entity names, captured fields, metadata date/location, vocab terms |
| `describe.py:_get_file_context()` | captured fields, derived tags |
| `suggest.py` | `get_enrichment_text_for_file()` + separate description + transcript queries |
| `retag.py` | description + vocab terms |

These assemblies overlap significantly. `_assemble_context()` in `summarize.py`
is the most complete version but is private to that stage. Other stages
reimplement subsets of it with slightly different query shapes and slightly
different field names.

### Proposed solution

A `FileContext` dataclass assembled once per file from a single DB pass:

```python
@dataclass
class FileContext:
    file_id:            int
    filename:           str
    description:        str | None
    transcript:         str | None
    transcript_attributed: bool        # True if speaker labels present
    derived_tags:       list[str]
    entity_names:       list[str]
    captured_fields:    list[dict]     # [{field_name, value, value_type}]
    metadata_date:      str | None
    metadata_location:  str | None
    enrichment_text:    str            # pre-assembled from metadata fields
    vocab_terms:        list[str]      # from knowledge.db
```

Factory and cache functions in a new `src/text/` module (parallel to
`src/media/`):

```
src/text/
    __init__.py
    context.py    — FileContext, build_file_context(), load_file_context()
```

`build_file_context(corpus_conn, kb_conn, file_id) -> FileContext`

`load_file_context(corpus_conn, file_id) -> FileContext | None` — reads from
an optional `file_context_cache` table (JSON blob); same opportunistic-cache
pattern as FrameSet.

### Payoff

- `summarize.py:_assemble_context()` → replaced entirely by `build_file_context()`
- `describe.py:_get_file_context()` → replaced by reading relevant fields from `FileContext`
- `suggest.py` text pool → reads `context.enrichment_text`, `context.description`, `context.transcript`
- Prompt construction in all three stages becomes testable without DB (inject a `FileContext`)
- `search_text.csv` export becomes a trivial serialisation of cached contexts

### Notes

- The cache path (persisted JSON) is lower priority than the in-memory path;
  the consistency and testability wins are immediate from the dataclass alone
- `vocab_terms` comes from `knowledge.db`, not `corpus.db`; `build_file_context()`
  takes both connections, consistent with how `suggest.py` and `retag.py` already operate
- One-way dependency: `src/text/` imports from `src/db/`; nothing imports from `src/text/`
  except stages

---

## 2. LLMSession — Shared Model Loading and Inference

### Problem

`describe.py`, `retag.py`, and `summarize.py` all load a `llama_cpp.Llama`
model and run inference. `describe.py` has the most elaborate setup:

- `_resolve_chat_format()` — infers chat format from model/mmproj filenames
- `_make_chat_handler()` — maps format string to the correct handler class
- Model loading with `n_gpu_layers`, `n_ctx`, `mmproj`, `verbose=False`
- Retry loop (`config.deep_seek`, `config.deep_seek_max_iter`) on empty or
  malformed responses

`retag.py` and `summarize.py` replicate the model loading and retry patterns
with minor variations. Retry logic in particular is easy to get subtly wrong
and is currently the most common source of silent inference failures.

### Proposed solution

A `LLMSession` context manager in `src/llm/`:

```
src/llm/
    __init__.py
    session.py    — LLMSession, TextSession, VisionSession
```

```python
class LLMSession:
    def __init__(self, model_path: str, config: Config, *, vision: bool = False): ...
    def __enter__(self) -> "LLMSession": ...
    def __exit__(self, *_): ...
    def generate(self, prompt: str, images: list[bytes] | None = None) -> str: ...
```

`generate()` handles:
- JSON decoding and structural validation for stages that expect JSON
- Retry up to `config.deep_seek_max_iter` on empty string or parse failure
- Logging retries at DEBUG level, final failures at WARNING

`VisionSession` extends `LLMSession` with `_make_chat_handler()` and
`_resolve_chat_format()` moved from `describe.py`.

Stage usage becomes:

```python
with VisionSession(config.vision_model, config) as session:
    for file in pending:
        result = session.generate(prompt, images=[frame.jpeg_bytes])
```

### Payoff

- Retry logic lives in one tested place instead of three
- Chat format detection tested independently of describe stage
- Model load/unload is guaranteed by context manager even on cancel or exception
- GPU memory is released reliably between stage runs
- `describe.py`, `retag.py`, `summarize.py` each lose 30–50 lines of boilerplate

### Notes

- Vision model and text model are different model types; `VisionSession` and
  `TextSession` should be distinct classes rather than a single class with a
  `vision=True` flag — the constructor arguments and handler setup differ enough
  to warrant separation
- `src/llm/` follows the same one-way import rule as `src/media/`: stages import
  from it; it does not import from stages

---

## 3. Stage Loop Runner

### Problem

Every stage body follows the same structural pattern:

```python
pending = get_pending_X_files(db)
total = len(pending)
for i, row in enumerate(pending):
    if cancel_event.is_set():
        break
    progress.update(i, total, f"Stage X: {i+1}/{total}")
    try:
        # work on row
        mark_X(db, file_id)
    except Exception as e:
        logger.error("Stage X: error on %s: %s", row["path"], e)
progress.done()
```

This is repeated across all ~12 stage modules. The loop skeleton is not the
interesting part of any stage; it is noise that makes stages longer and
introduces subtle inconsistencies (some stages log at ERROR, some at WARNING;
some include the file path, some do not).

### Proposed solution

A thin `run_stage_loop()` helper in `src/pipeline/`:

```python
def run_stage_loop(
    pending: list,
    process: Callable[[Any], None],
    progress: ProgressReporter,
    cancel_event: threading.Event,
    *,
    label: str = "item",
) -> int:
    """Run process(row) for each row; return count of successful items."""
```

Each stage reduces to:

```python
pending = get_pending_X_files(db)
run_stage_loop(pending, lambda row: _process_file(db, row), progress, cancel_event, label="describe")
```

`run_stage_loop` handles: cancel check, progress update, exception catch and
log, done signal. It does not touch the DB or business logic.

### Payoff

- Eliminates ~15 lines of identical boilerplate per stage
- Consistent error logging format across all stages
- Cancel behaviour is guaranteed consistent — no stage accidentally omits the
  cancel check

### Notes

- The helper must stay shallow — it handles the loop/cancel/progress/error
  skeleton only, never DB calls or business logic
- Stages that have more complex loop behaviour (e.g., batch processing, two-pass
  loops) may not fit the simple form and should not be forced to use it
- Lower priority than FileContext and LLMSession; existing stages work correctly,
  this is purely a maintainability improvement

---

## 4. Cluster Result Typing

### Problem

`face.py`, `voice.py`, and `gps_cluster.py` all produce cluster assignments
of the form `file_id → cluster_id`, with a centroid or representative value and
an optional person link. The three implementations use different column names,
different query shapes, and different update patterns, which makes the export
stage and review UI handle each cluster type with bespoke code.

### Proposed solution

A shared `ClusterAssignment` dataclass:

```python
@dataclass
class ClusterAssignment:
    file_id:    int
    cluster_id: int
    score:      float | None    # similarity to centroid
    person_id:  int | None      # None if unassigned
```

And a protocol for cluster tables so the export stage and review API can
iterate over any cluster type with the same query shape.

### Payoff

- Export stage: one CSV writer per cluster type instead of three bespoke ones
- Review UI: shared HTMX partials for cluster-to-person assignment
- Future cluster types (e.g., scene clusters from video, topic clusters from
  transcripts) slot in automatically

### Notes

- Smallest scope of the four; existing cluster code works correctly
- Most valuable when a fourth cluster type is added; the case for refactoring
  three existing types is weaker
- Defer until there is a concrete fourth cluster type or until export/review
  complexity from the current three becomes a maintenance burden
