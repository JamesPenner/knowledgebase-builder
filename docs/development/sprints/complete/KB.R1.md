# Sprint KB.R1 — Summarize Stage (3c)

## Goal

Implement Stage 3c: an LLM-based synthesis stage that reads `descriptions` and
`transcriptions` outputs and produces a single per-file summary stored in
`file_summaries`. The summary is domain-aware (corpus context injection),
vocabulary-guided (soft injection of accepted terms), and handles both
single-window and hierarchical chunked paths for long transcripts. Summaries
feed into the Suggest text pool and can optionally be written to file metadata
via the existing write-back stage.

## Baseline

1044 tests (committed at KB.Q4).

## Scope

- Stage implementation (`stages/summarize.py`) + corpus migration
- Config additions: `summarize_target_words`, `summarize_max_transcript_tokens`,
  `summarize_output_field`
- DB helpers in `db/corpus.py`
- DAG wiring (`pipeline/dag.py`)
- API Pattern 1 routes (`api/pipeline.py`)
- CLI command (`cli/pipeline.py`)
- Export: `summaries.csv` added to `stages/export.py`
- Write-back: reads `file_summaries.summary_text` when `summarize_output_field`
  is configured

GPU/LLM inference is **not tested in CI**. Unit tests cover prompt building,
chunking logic, and response parsing. Integration tests cover schema, DB
helpers, DAG, API, CLI, export, and write-back config reading.

## Deliverables

### New files

- `src/stages/summarize.py` — Stage 3c implementation
- `src/migrations/corpus/0018_file_summaries.sql` — `file_summaries` table
- `tests/unit/test_summarize_unit.py` — prompt building, chunking, parsing
- `tests/integration/test_summarize_integration.py` — schema, DB helpers,
  API, CLI, export, write-back config

### Modified files

- `src/config.py` — three new config fields
- `src/db/corpus.py` — four new DB helpers
- `src/pipeline/dag.py` — summarize in DEPENDENCIES, INVALIDATES; describe +
  transcribe INVALIDATES updated
- `src/api/pipeline.py` — `SummarizeRunRequest` + Pattern 1 routes
- `src/cli/pipeline.py` — `enrich summarize` command
- `src/stages/export.py` — `_write_summaries` + call in `run_export`
- `src/stages/writeback.py` — read summary text when `summarize_output_field`
  is set
- `tests/integration/test_schema.py` — verify `file_summaries` columns after
  migration

---

## Migration (`src/migrations/corpus/0018_file_summaries.sql`)

```sql
CREATE TABLE IF NOT EXISTS file_summaries (
    file_id       INTEGER PRIMARY KEY REFERENCES files(id),
    summary_text  TEXT,
    model         TEXT,
    prompt_version TEXT,
    processed_at  DATETIME,
    status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','done','failed','skipped'))
);
```

---

## Config (`src/config.py`)

Three new fields on the `Config` dataclass, all parsed from the `summarize:`
section of `library.yaml`:

```python
summarize_target_words: int = 150
summarize_max_transcript_tokens: int = 18000
summarize_output_field: str = ""   # CanonicalName; empty = store only, no write-back
```

Config loading already parses top-level YAML sections by key name. Add a
`summarize:` block parser alongside the existing `describe:`, `write_back:`,
etc. blocks. `corpus_context` and `focus` are already on the config as
`config.focus` (per-KB only). Summarize reads `config.focus` directly — no
new config field needed.

---

## DB Helpers (`src/db/corpus.py`)

```python
def get_pending_summarize_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Files eligible for summarize: have a description OR a transcription,
    and whose file_summaries row is pending/failed/skipped or does not exist.
    Returns canonical files only (canonical_id IS NULL)."""

def upsert_file_summary(
    conn: sqlite3.Connection,
    file_id: int,
    summary_text: str | None,
    model: str,
    prompt_version: str,
    status: str,
) -> None:
    """INSERT OR REPLACE into file_summaries."""

def get_file_summary(
    conn: sqlite3.Connection,
    file_id: int,
) -> sqlite3.Row | None:
    """Return the file_summaries row for file_id, or None."""

def get_export_summaries(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return rows where status='done', joined to files.path.
    Columns: file_path, summary_text, model, processed_at."""
```

`get_pending_summarize_files` query skeleton:

```sql
SELECT f.id
FROM files f
WHERE f.canonical_id IS NULL
  AND (
      EXISTS (SELECT 1 FROM descriptions d
              WHERE d.file_id = f.id AND d.pass1_status = 'done')
   OR EXISTS (SELECT 1 FROM transcriptions t
              WHERE t.file_id = f.id AND t.transcribe_status = 'done')
  )
  AND NOT EXISTS (
      SELECT 1 FROM file_summaries s
      WHERE s.file_id = f.id AND s.status = 'done'
  )
ORDER BY f.id
```

---

## Stage (`src/stages/summarize.py`)

### Module-level constants

```python
_PROMPT_VERSION = "v1"

_SYSTEM_PROMPT = """\
You are a metadata summarization assistant. Write a factual, searchable summary
of a media file. Respond with plain text only — no bullet points, no headings,
no explanation outside the summary itself.\
"""
```

### Public function

```python
def run_summarize(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
```

Follows the retag pattern exactly:

1. Guard: return early (with warning log) if `config.text_model` is empty.
2. Lazy-import `llama_cpp`; return with error log if not installed.
3. Load text model with `config.text_model` + `config.text_gpu_layers`.
4. `ATTACH` knowledge.db to read accepted vocabulary terms.
5. Load pending files via `get_pending_summarize_files`.
6. Per file: assemble context, select prompt case, call LLM, write result.
7. Batch DB commits every 10 files.
8. Call `update_pipeline_checkpoint` on completion.

### Internal functions

```python
def _assemble_context(corpus_conn, kb_conn, file_id: int) -> dict:
    """Collect all available inputs for a file: description, transcript
    (attributed if segments+speaker_labels exist, else flat), derived tags,
    entity matches, normalized filename, captured date/location, accepted
    vocabulary terms. Returns a dict with keys:
      description, transcript, attributed, derived_tags, entity_names,
      normalized_filename, captured_date, captured_location, vocab_terms."""

def _build_prompt(ctx: dict, focus: str, target_words: int) -> str:
    """Select Case 1 / 2 / 3 based on ctx, assemble system + user prompt.
    Returns the full formatted prompt string."""

def _chunk_transcript(
    transcript: str, max_tokens: int, overlap_ratio: float = 0.1
) -> list[str]:
    """Split transcript into overlapping token-budget chunks.
    Splits on whitespace; overlap = round(len(chunk) * overlap_ratio) words."""

def _summarize_chunks(llm, chunks: list[str], focus: str) -> str:
    """Summarize each chunk independently, then call the LLM once more to
    synthesise the chunk summaries into a single paragraph."""

def _call_llm(llm, prompt: str, max_tokens: int = 512) -> str:
    """Single LLM call; returns stripped text output. Returns '' on failure."""
```

### Prompt cases

**System prefix shared by all cases:**
```
{_SYSTEM_PROMPT}
{f"DOMAIN FOCUS: {focus}" if focus else ""}
```

**Context block shared by all cases (omit empty lines):**
```
File: {normalized_filename}
Date: {captured_date}
Location: {captured_location}
Tags: {", ".join(derived_tags)}
Relevant vocabulary (use where genuinely present): {", ".join(vocab_terms)}
```

**Case 1 — description only:**
```
Visual description:
{description}

Write a {target_words}-word summary for use as searchable metadata.
```

**Case 2 — transcript only:**
```
{"Attributed t" if attributed else "T"}ranscript:
{transcript}

Write a {target_words}-word summary for use as searchable metadata.
```

**Case 3 — description + transcript:**
```
Visual description:
{description}

{"Attributed t" if attributed else "T"}ranscript:
{transcript}

Write a {target_words}-word summary integrating both the visual and audio
content. Where they are complementary, combine them. Where they diverge,
note both.
```

### Long-transcript path

If `len(transcript.split()) * 1.3 > config.summarize_max_transcript_tokens`
(rough words-to-tokens estimate), call `_chunk_transcript` and
`_summarize_chunks` to produce a condensed transcript before building the
main prompt. The condensed output replaces `transcript` in the context dict.

### Skip logic

If neither description nor transcript is available for a file (both missing or
failed), call `upsert_file_summary` with `status='skipped'` and
`summary_text=None`. Do not call the LLM.

---

## DAG (`src/pipeline/dag.py`)

**DEPENDENCIES** — add:
```python
"summarize": ["describe", "transcribe"],
```

Suggest already depends on `["describe", "transcribe"]`. Summarize follows
the same pattern: both upstream stages must have been run (even if they
produced no output for individual files), and the stage handles missing
per-file data gracefully.

**INVALIDATES** — add `"summarize"` to describe and transcribe entries; add
new summarize entry:
```python
"describe":   [...existing..., "summarize"],
"transcribe": [...existing..., "summarize"],
"summarize":  ["suggest"],
```

---

## API (`src/api/pipeline.py`)

Pattern 1 routes for the `summarize` DAG key — identical structure to the
existing `describe` and `retag` route groups.

```python
class SummarizeRunRequest(BaseModel):
    force: bool = False
```

Routes:
```
POST   /api/stages/summarize/run     → {"job_id": str, "status": "started"}
POST   /api/stages/summarize/cancel  → {"status": "cancelled"}
GET    /api/stages/summarize/status  → {"status": str, "current": int, "total": int, ...}
GET    /api/stages/summarize/stream  → text/event-stream
```

---

## CLI (`src/cli/pipeline.py`)

```python
@app.command("summarize")
def summarize_cmd(
    kb: str = typer.Option(...),
    force: bool = typer.Option(False, "--force"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """Stage 3c: synthesise describe + transcribe outputs into per-file summaries."""
```

Calls `run_summarize(corpus_path, kb_path, config, progress, cancel_event)`.
Follows the same pattern as the `describe` and `retag` commands.

---

## Export (`src/stages/export.py`)

```python
def _write_summaries(export_dir: Path, corpus_conn) -> None:
    from src.db.corpus import get_export_summaries
    rows = get_export_summaries(corpus_conn)
    if not rows:
        return
    with open(export_dir / "summaries.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["file_path", "summary_text", "model", "processed_at"]
        )
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "file_path": r["file_path"],
                "summary_text": r["summary_text"],
                "model": r["model"],
                "processed_at": r["processed_at"],
            })
```

Add `_write_summaries(export_dir, corpus_conn)` to `run_export` alongside the
other `_write_*` calls.

---

## Write-back (`src/stages/writeback.py`)

When `config.summarize_output_field` is non-empty, write-back must write
`file_summaries.summary_text` to the resolved ExifTool tag.

Add a helper:

```python
def _resolve_summarize_field(
    kb_folder: Path, canonical_name: str
) -> dict | None:
    """Look up the ExifTool field_name for canonical_name in field_map.csv.
    Returns {"field_name": str, "canonical_name": str, "value_type": "text"}
    or None if not found."""
```

In `run_writeback`, after resolving `write_fields`, if
`config.summarize_output_field` is set:

1. Call `_resolve_summarize_field` to find the ExifTool tag.
2. Per file in the stale set, query `file_summaries.summary_text` where
   `status='done'`.
3. If summary_text is non-null, include it as an additional ExifTool write
   alongside keywords and description.

The summary field takes precedence over `descriptions.description_normalized`
if both resolve to the same tag — checked by comparing `field_name` values.

---

## Suggest text pool update (`src/stages/suggest.py`)

Stage 4 Level A assembles per-file text from multiple sources. Add
`file_summaries.summary_text` to the query:

```sql
LEFT JOIN file_summaries fs ON fs.file_id = COALESCE(f.canonical_id, f.id)
    AND fs.status = 'done'
```

Include `fs.summary_text` in the text assembled per file, with the same
"omit if NULL" logic applied to descriptions and transcriptions.

---

## Test Targets

### `tests/unit/test_summarize_unit.py`

Prompt building (no DB, no LLM):
- `test_build_prompt_case1_description_only` — case 1 selected; expected sections present
- `test_build_prompt_case2_transcript_only` — case 2 selected
- `test_build_prompt_case3_combined` — case 3 selected; both sections present
- `test_build_prompt_injects_corpus_context` — focus string appears in output
- `test_build_prompt_injects_vocabulary` — vocab terms appear with soft-guidance wording
- `test_build_prompt_attributed_transcript` — "Attributed transcript" label when attributed=True
- `test_build_prompt_empty_context_skips_blank_lines` — no empty context lines rendered

Chunk splitting:
- `test_chunk_transcript_below_threshold` — single chunk returned unchanged
- `test_chunk_transcript_above_threshold` — produces multiple chunks
- `test_chunk_overlap_preserves_words` — last N words of chunk[i] appear at start of chunk[i+1]

Response handling:
- `test_call_llm_returns_stripped_text` — whitespace stripped from output (mock LLM)
- `test_call_llm_empty_returns_empty_string` — empty response handled without exception

**Subtotal: 12 unit tests**

### `tests/integration/test_summarize_integration.py`

Schema:
- `test_file_summaries_table_exists` — migration creates table with expected columns

DB helpers (real SQLite in tmp_path):
- `test_get_pending_summarize_files_empty` — empty corpus returns []
- `test_get_pending_summarize_files_with_description` — file with done description returned
- `test_get_pending_summarize_files_with_transcription` — file with done transcription returned
- `test_get_pending_summarize_files_skips_done` — file already done not returned
- `test_upsert_file_summary_insert` — row created with correct values
- `test_upsert_file_summary_update` — repeat call updates existing row
- `test_get_file_summary_found` — returns correct row
- `test_get_file_summary_missing` — returns None
- `test_get_export_summaries_filters_done` — only done rows returned
- `test_get_export_summaries_empty` — returns [] when no done rows

API (Pattern 1 wiring, no inference):
- `test_summarize_run_returns_job_id` — POST /api/stages/summarize/run → 200 with job_id
- `test_summarize_cancel_returns_cancelled` — POST /api/stages/summarize/cancel → 200
- `test_summarize_status_endpoint` — GET /api/stages/summarize/status → valid shape

CLI:
- `test_summarize_command_exists` — `enrich summarize --help` exits 0

Export:
- `test_write_summaries_produces_csv` — summaries.csv written when done rows exist
- `test_write_summaries_skipped_when_empty` — no summaries.csv when no done rows

Write-back:
- `test_resolve_summarize_field_found` — canonical_name matched in field_map.csv
- `test_resolve_summarize_field_missing` — returns None for unknown canonical_name
- `test_resolve_summarize_field_empty_config` — empty summarize_output_field skips lookup

DAG:
- `test_summarize_in_dependencies` — 'summarize' key present; deps include describe + transcribe
- `test_describe_invalidates_summarize` — 'summarize' in INVALIDATES['describe']
- `test_transcribe_invalidates_summarize` — 'summarize' in INVALIDATES['transcribe']
- `test_summarize_invalidates_suggest` — 'suggest' in INVALIDATES['summarize']

Suggest text pool:
- `test_suggest_level_a_includes_summary_text` — summary text appears in assembled per-file text

Schema (test_schema.py addition):
- `test_file_summaries_schema` — columns: file_id, summary_text, model, prompt_version,
  processed_at, status

**Subtotal: 27 integration tests (including 1 in test_schema.py)**

**Total new tests: 12 + 27 = 39**

**Target: 1044 → 1083+ (+39)**

---

## Acceptance Criteria

1. `src/migrations/corpus/0018_file_summaries.sql` applied cleanly; `test_schema.py` passes
2. `get_pending_summarize_files` returns files that have a `done` description or transcription
   and no `done` summary
3. `enrich summarize --kb <name>` runs without error on a corpus with no text model configured
   (skips with log warning, does not crash)
4. `enrich summarize --kb <name> --force` resets `done` summaries to `pending` and re-queues them
5. `enrich export --kb <name>` produces `summaries.csv` when done summaries exist; omits it when
   there are none
6. When `summarize_output_field` is set in `library.yaml`, `run_writeback` writes
   `summary_text` to the resolved ExifTool tag for files with `status='done'`
7. `_chunk_transcript` produces overlapping chunks when transcript word count exceeds threshold
8. Suggest Level A text pool includes `file_summaries.summary_text` where status='done'
9. DAG: `summarize` depends on `['describe', 'transcribe']`; `describe` and `transcribe`
   INVALIDATES include `'summarize'`; `summarize` INVALIDATES includes `'suggest'`
10. All 1083+ tests pass; ruff clean
