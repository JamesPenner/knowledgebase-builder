# Sprint KB.S4 — FileContext: Unified Per-File Text Assembly

## Scope

Introduce `src/text/context.py` with a `FileContext` dataclass and
`build_file_context()` factory function. Replace four overlapping per-stage
text assembly functions with a single consistent source. Make prompt
construction in LLM stages testable without a database connection.

No schema changes. No new API endpoints or CLI commands. No UI changes.

## Builds On

- KB.S1 (LLMSession): LLM stages use `session.generate(system, user)` with
  explicit prompt parameters — `FileContext` becomes the source of the `user`
  argument content
- KB.R1 (Summarize): `_assemble_context()` in summarize.py is the most
  complete existing assembly; it becomes the reference implementation for
  `build_file_context()`

## Baseline

Record current test count at sprint start (see ROADMAP.md).

## Deliverables

### New module

**`src/text/__init__.py`** — empty package marker

**`src/text/context.py`**

```python
@dataclass
class FileContext:
    file_id:               int
    filename:              str
    description:           str | None
    transcript:            str | None
    transcript_attributed: bool          # True if speaker labels present
    summary_text:          str | None    # from file_summaries (status='done')
    derived_tags:          list[str]
    entity_names:          list[str]
    captured_fields:       list[dict]    # [{field_name, value, value_type}]
    metadata_date:         str | None
    metadata_location:     str | None
    enrichment_text:       str           # pre-assembled from metadata fields
    vocab_terms:           list[str]     # from knowledge.db vocabulary

def build_file_context(
    corpus_conn: sqlite3.Connection,
    kb_conn: sqlite3.Connection | None,
    file_id: int,
) -> FileContext:
    """Assemble all available enrichment data for a file in one pass."""
```

**One-way import rule:** `src/text/` imports from `src/db/` only (named query
functions). Nothing imports from `src/text/` except stage modules. `src/text/`
never imports from `src/stages/`, `src/media/`, or `src/llm/`.

**Query coverage** (what `build_file_context()` fetches):

| Field | Source table |
|---|---|
| `description` | `descriptions` (normalised preferred, raw fallback) |
| `transcript` | `transcript_segments` (attributed) → `transcriptions` (plain) |
| `transcript_attributed` | True if any segment has `speaker_label` |
| `derived_tags` | `file_derived_tags` |
| `entity_names` | `file_entity_matches` (non-stale) |
| `captured_fields` | `file_captured_fields` + `capture_rules.value_type` |
| `metadata_date` | `file_metadata_fields` (canonical_name='captured_date') |
| `metadata_location` | `file_geolabels` (custom_region, state, country) |
| `summary_text` | `file_summaries` (status='done') |
| `enrichment_text` | assembled from metadata fields (reuses existing logic) |
| `vocab_terms` | `knowledge.db vocabulary` (source IN 'accepted','user') |
| `filename` | `files.filename` |

`kb_conn=None` is valid; `vocab_terms` is empty in that case. All fields
default gracefully when the source table has no row for `file_id`.

### Modified modules

**`src/stages/summarize.py`**

- Remove `_assemble_context()` — replaced by `build_file_context()`
- `run_summarize()` calls `build_file_context(corpus_conn, kb_conn, file_id)`
- `_build_user_prompt(ctx: dict, ...)` → `_build_user_prompt(ctx: FileContext, ...)`
- `_build_system_prompt()` unchanged (does not depend on per-file data)

**`src/stages/describe.py`**

- Remove `_get_file_context()` — replaced by `build_file_context()`
- `run_describe()` calls `build_file_context()` and passes `ctx.captured_fields`,
  `ctx.derived_tags` to `_build_describe_prompt()`
- `_build_describe_prompt(captured_fields, derived_tags, focus)` gains an explicit
  `base_prompt: str = _BASE_PROMPT` parameter:
  `_build_describe_prompt(captured_fields, derived_tags, focus, base_prompt=_BASE_PROMPT)`
  The default preserves current behaviour; the Prompt Library sprint will supply a
  KB-loaded string without changing the function's internals.
- `run_describe_file()` (stateless quick-describe path, no DB) is **unchanged** —
  it continues to call `_build_describe_prompt([], [], focus)` with empty lists.
- `_build_describe_prompt` input types remain primitives (`list[dict]`, `list[str]`,
  `str`) so prompt construction is testable without constructing a `FileContext`.

**`src/stages/suggest.py`**

- Replace per-file queries in **both** `_run_level_a` and `_run_level_c` with
  `build_file_context()` (Level C builds sample file texts for its cluster prompt
  using the same inline queries as Level A — both are replaced).
- Level A text pool reads `ctx.enrichment_text`, `ctx.description`,
  `ctx.summary_text`, `ctx.derived_tags` from `FileContext`.
- Level C sample text reads `ctx.enrichment_text`, `ctx.description` from
  `FileContext`.

**`src/stages/retag.py`**

- Replace the inline `description` and `derived` queries with
  `build_file_context()`
- `_build_prompt()` reads `ctx.description` (normalised preferred),
  `ctx.derived_tags`, `ctx.vocab_terms` from `FileContext`

**`src/db/corpus.py`** / **`src/db/kb.py`**

- Any new named query functions needed by `build_file_context()` that do not
  already exist (e.g. `get_file_enrichment_text` if currently inline in suggest)

## Acceptance Criteria

1. `python -m pytest tests/ -q` → all prior tests pass; ≥ 10 new tests pass
2. `ruff check src/ tests/` → 0 errors
3. `from src.text.context import FileContext, build_file_context` succeeds
4. `_assemble_context` is no longer defined in `summarize.py`
5. `_get_file_context` is no longer defined in `describe.py`
6. `build_file_context(corpus_conn, None, file_id)` succeeds and returns a
   `FileContext` with `vocab_terms=[]`
7. Prompt construction in summarize, retag, and suggest can be tested by
   constructing a `FileContext` directly. `_build_describe_prompt` takes plain
   list inputs (already DB-free) and is tested by passing lists directly.
8. `_build_describe_prompt` accepts a `base_prompt` keyword argument; passing a
   custom string changes the base instruction in the output.

## Test Targets — ~14 new tests

All in `tests/unit/test_file_context.py`. No real DB required for prompt
construction tests; `FileContext` is constructed directly (or plain lists for
the describe tests).

### `build_file_context()` integration tests (real SQLite in tmp_path)

- `test_build_context_description_only` — file with description, no transcript;
  `ctx.description` populated, `ctx.transcript` is None
- `test_build_context_attributed_transcript` — transcript segments with
  speaker labels; `ctx.transcript_attributed=True`, transcript formatted with
  speaker prefixes
- `test_build_context_plain_transcript_fallback` — no segments; falls back to
  `transcriptions` table; `ctx.transcript_attributed=False`
- `test_build_context_entity_names` — file with entity matches;
  `ctx.entity_names` non-empty
- `test_build_context_metadata_date_and_location` — metadata fields and
  geolabels present; `ctx.metadata_date` and `ctx.metadata_location` populated
- `test_build_context_summary_text` — file with a `done` summary; `ctx.summary_text`
  populated; a file with `failed` summary returns `ctx.summary_text=None`
- `test_build_context_no_kb_conn` — `kb_conn=None`; returns valid `FileContext`
  with `vocab_terms=[]`
- `test_build_context_empty_file` — file with no enrichment data; all optional
  fields are None or empty; does not raise

### Prompt construction unit tests (no DB)

- `test_describe_prompt_uses_captured_fields` — pass a captured date field as a
  list; `_build_describe_prompt()` includes the date in output
- `test_describe_prompt_uses_base_prompt_override` — pass `base_prompt="custom
  instruction"`; output ends with the custom string, not `_BASE_PROMPT`
- `test_retag_prompt_includes_vocab_terms` — `FileContext` with vocab_terms;
  `_build_prompt()` includes terms in output
- `test_summarize_prompt_description_and_transcript` — `FileContext` with both;
  `_build_user_prompt()` includes both sections
- `test_summarize_prompt_transcript_only` — `FileContext` with no description;
  prompt contains only transcript section
- `test_suggest_text_pool_uses_context_fields` — `FileContext` with description,
  enrichment_text, and summary_text; text pool assembly includes all three

## Design Notes

### Why `FileContext`, not just shared query functions

The goal is not only to eliminate duplicate SQL — it is to make prompt
construction testable without a database. A `FileContext` can be constructed
with known values in a unit test; the prompt functions then become pure
functions of their inputs. This is the same principle as `TextSession`:
separate the "gather data" step from the "use data" step so both are
independently testable.

### `enrichment_text` field

`suggest.py` currently calls `get_enrichment_text_for_file()` which
assembles a text string from captured metadata fields. This logic is absorbed
into `build_file_context()` and the result stored in `ctx.enrichment_text`.
Stages that previously called `get_enrichment_text_for_file()` now read
`ctx.enrichment_text` directly.

### Why `_build_describe_prompt` keeps primitive inputs

`describe.py` has two call sites: the pipeline stage (`run_describe`, has DB
connections) and the stateless quick-describe path (`run_describe_file`, no DB).
Changing `_build_describe_prompt` to accept `FileContext` would force
`run_describe_file` to construct a dummy dataclass just to pass empty lists —
noisy, and the function's actual inputs (`captured_fields`, `derived_tags`) are
already plain primitives with no DB dependency. Testability without DB is
achieved without the signature change. The data-gathering side is still
consolidated: `run_describe` calls `build_file_context()` and passes
`ctx.captured_fields`/`ctx.derived_tags`. `run_describe_file` is unchanged.

### `_build_describe_prompt` and the Prompt Library

`_BASE_PROMPT` ("Describe this image in detail…") is the core per-image
instruction and should be user-editable per-KB. KB.S4 adds an explicit
`base_prompt: str = _BASE_PROMPT` parameter to `_build_describe_prompt` so the
function accepts any instruction string without touching its internals. The
Prompt Library sprint will then supply `load_stage_prompt(kb_conn, "describe",
"base", _BASE_PROMPT)` at the call site — no further refactoring needed.

### Prerequisite for Prompt Library

The Prompt Library sprint (`PROMPT_LIBRARY_CONCEPT.md`) stores stage prompts
in `knowledge.db` and loads them at runtime. The loading function signature is
`load_stage_prompt(kb_conn, stage, prompt_key, default) -> str`. After KB.S4,
the `system` string passed to `session.generate(system, user)` comes from
`load_stage_prompt()` rather than a module constant. The `user` string is
assembled from `FileContext`. KB.S4 also wires in `base_prompt` for describe's
user-side instruction. Both halves are needed for the full prompt management
story.

### `suggest.py` text pool and Level C

`suggest.py` builds per-file text in two places: the Level A frequency analysis
(every file) and the Level C cluster labelling (up to 5 sample files per
cluster). Both currently contain overlapping inline queries. Both are replaced
by `build_file_context()`. Level A text pool gains `ctx.summary_text` as an
additional source — summaries were already being read inline but are now folded
into the canonical `FileContext` representation.

## Out of Scope

- Prompt Library (per-KB named prompts in knowledge.db) — `PROMPT_LIBRARY_CONCEPT.md`
- `FileContext` cache (JSON blob in DB) — deferred; in-memory path covers
  the consistency and testability wins immediately
- `search_text.csv` export using `FileContext` — deferred to export improvements
- Any visual or audio preparation changes (KB.S2, KB.S3)
