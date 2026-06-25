# Sprint KB.S5 — Prompt Library

## Scope

Introduce a per-KB prompt library stored in `knowledge.db`. Users can view,
edit, create, and activate prompt variants for the three LLM stages (Describe,
Retag, Summarize) without touching source code. Built-in defaults are seeded at
KB creation; user variants override them by activation.

Quick-describe (`enrich quick describe`) gains an optional `--kb` flag so spot-
checks can use the active KB prompt rather than the hardcoded default — the
primary use case is testing a custom prompt before committing GPU time to a
full pipeline run.

## Builds On

- KB.S1 (LLMSession): `generate(system, user)` signature is already in place;
  the prompt library slots into the seam it created
- KB.S4 (FileContext): LLM stages already load context once before the per-file
  loop; prompts load in the same place

## Baseline

1196 tests, 2 skipped, ruff clean (as of KB.S4 completion).

## Prompt Keys in Scope

Four keys covering all current LLM stage prompts:

| `stage`     | `prompt_key` | Current source in code                                  |
|-------------|--------------|--------------------------------------------------------|
| `describe`  | `system`     | `_BASE_PROMPT` in `describe.py`                        |
| `describe`  | `aggregate`  | Instruction suffix inline in `_aggregate_descriptions` |
| `retag`     | `system`     | `_SYSTEM_PROMPT` constant in `retag.py`                |
| `summarize` | `system`     | Base text of `_build_system_prompt()` in `summarize.py`|

**`describe/frame` is not included.** In current code, images and video frames
use the same base prompt via `_build_describe_prompt`; a separate frame key
would be a no-op. It can be added if differentiated per-frame instructions
become a real need.

**Config-level override (`config.yaml: prompts:`) is deferred.** The DB active-
prompt mechanism is sufficient, and `Config` is a frozen dataclass that does not
accommodate a free-form dict field without meaningful complexity.

## Deliverables

---

### 1. Migration — `src/migrations/knowledge/0003_stage_prompts.sql`

```sql
CREATE TABLE IF NOT EXISTS stage_prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stage       TEXT    NOT NULL,
    prompt_key  TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 0,
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    DEFAULT (datetime('now')),
    UNIQUE (stage, prompt_key, name)
);
```

---

### 2. DB helpers — `src/db/kb.py`

Six new functions added to `kb.py`. `seed_stage_prompts` is called from
`open_kb()` immediately after `apply_migrations()`.

#### `seed_stage_prompts(kb_conn) -> None`

`INSERT OR IGNORE` the four built-in rows with `is_builtin=1, is_active=1`.
Safe to call on existing KBs — idempotent by the UNIQUE constraint. The body
for each row is the current module-level constant imported at seed time:
- `describe/system` ← `_BASE_PROMPT` from `describe.py`
- `describe/aggregate` ← `_AGGREGATE_INSTRUCTION` constant extracted from
  `_aggregate_descriptions` in `describe.py`
- `retag/system` ← `_SYSTEM_PROMPT` from `retag.py`
- `summarize/system` ← `_SUMMARIZE_BASE` constant extracted from
  `_build_system_prompt()` in `summarize.py` (focus line excluded — appended at
  runtime)

#### `load_stage_prompt(kb_conn, stage: str, prompt_key: str, default: str) -> str`

Queries for the single `is_active=1` row matching `(stage, prompt_key)`.
Returns `default` if no active row exists or if the table is absent (handles
KBs created before this migration).

#### `list_stage_prompts(kb_conn) -> list[dict]`

Returns all rows ordered by `stage, prompt_key, is_builtin DESC, name`. Used
by the web UI to render the full prompt list.

#### `upsert_stage_prompt(kb_conn, stage: str, prompt_key: str, name: str, body: str) -> int`

`INSERT OR REPLACE` a user prompt row (`is_builtin=0`). Returns the row `id`.
Commits the connection.

#### `set_active_stage_prompt(kb_conn, stage: str, prompt_key: str, prompt_id: int) -> None`

Runs in a single transaction:
1. `UPDATE stage_prompts SET is_active=0 WHERE stage=? AND prompt_key=?`
2. `UPDATE stage_prompts SET is_active=1 WHERE id=?`

Raises `ValueError` if no row exists with the given `prompt_id`.

#### `delete_stage_prompt(kb_conn, prompt_id: int) -> None`

Raises `ValueError` if the target row has `is_builtin=1`.
Deletes the row and commits. If the deleted row was active, the builtin for
that `(stage, prompt_key)` is automatically reactivated as part of the same
transaction.

---

### 3. Stage wiring

Each LLM stage loads its prompt(s) **once before the per-file loop** and passes
them as parameters into the functions that call `session.generate()`. No inline
DB calls per file.

#### `describe.py`

- Extract the aggregation instruction string from `_aggregate_descriptions` into
  a module-level constant `_AGGREGATE_INSTRUCTION`.
- `_aggregate_descriptions(frame_descriptions, focus, session, instruction=_AGGREGATE_INSTRUCTION)` —
  add `instruction` parameter; replace the hardcoded string.
- In `run_describe()`, load once before the loop:
  ```python
  base_prompt = load_stage_prompt(kb_conn, "describe", "system", default=_BASE_PROMPT)
  aggregate_instruction = load_stage_prompt(kb_conn, "describe", "aggregate", default=_AGGREGATE_INSTRUCTION)
  ```
- Pass `base_prompt` to `_build_describe_prompt(…, base_prompt=base_prompt)`.
- Pass `aggregate_instruction` to `_aggregate_descriptions(…, instruction=aggregate_instruction)`.

#### `run_describe_file` (quick-describe)

Add optional `kb_path: Path | None = None` parameter:

```python
def run_describe_file(
    path: Path,
    config: Config,
    focus: str = "",
    db=None,
    kb_path: Path | None = None,
) -> str | None:
```

If `kb_path` is not `None`: open KB connection, load `describe/system` and
`describe/aggregate`, close connection immediately. Otherwise use module-level
constants. This keeps the "no KB required" default intact.

#### `retag.py`

Load once before the loop:
```python
system_prompt = load_stage_prompt(kb_conn, "retag", "system", default=_SYSTEM_PROMPT)
```
Pass `system_prompt` to `session.generate(system_prompt, prompt)` instead of
the module-level constant.

#### `summarize.py`

- Extract the base text from `_build_system_prompt` into a module-level
  constant `_SUMMARIZE_BASE`.
- Add `base` parameter: `_build_system_prompt(focus: str, base: str = _SUMMARIZE_BASE) -> str`.
  The function assembles `base + ("\nDOMAIN FOCUS: {focus}" if focus else "")`.
- Refactor `_summarize_chunks(session, chunks, system)` to accept the already-
  built system string (removes the internal `_build_system_prompt` call inside
  `_summarize_chunks`; the caller builds it once and passes it).
- In `run_summarize()`, load once before the loop:
  ```python
  base_system = load_stage_prompt(kb_conn, "summarize", "system", default=_SUMMARIZE_BASE)
  system = _build_system_prompt(config.focus, base=base_system)
  ```
  Pass `system` to both `session.generate` and `_summarize_chunks`.

---

### 4. CLI update — `src/cli/quick.py`

Add optional `--kb` option to `quick_describe`:

```python
kb: str | None = typer.Option(None, "--kb", help="Path to KB directory; loads active describe prompt")
```

Update the docstring: `"Vision describe — uses active KB prompt when --kb is given."`.

If `--kb` is provided:
- Resolve to a `Path`. If the path does not exist or contains no `knowledge.db`,
  print a warning and fall through to the default prompt (do not abort — the
  describe run is still valid).
- Pass `kb_path=Path(kb)` to `run_describe_file`.

No change to `quick_transcribe` — transcription has no configurable LLM prompt.

---

### 5. Web UI

**New template:** `templates/prompt_library.html`

**New page route** in `src/api/ui.py`:
```
GET /knowledge/prompts
```
Loads all prompts via `list_stage_prompts`; renders grouped by stage, then by
prompt key. Each group shows all variants with an "Active" badge on the current
one.

**New API handlers** in `src/api/knowledge.py` (mounted at `/api/knowledge/`; KB
resolved via `?kb=name` query param, not a path segment):
```
POST   /api/knowledge/prompts              create user prompt
PUT    /api/knowledge/prompts/{id}         update body of existing user prompt
POST   /api/knowledge/prompts/{id}/activate  set as active for its (stage, prompt_key)
DELETE /api/knowledge/prompts/{id}         delete (400 if builtin)
```

All four follow the existing Pattern 2 shape (HTMX partial swap on success).
Activate and delete return the updated prompt group partial so HTMX can swap
the section in place without a full page reload.

**Nav:** Add "Prompts" link at the end of the Knowledge section in
`templates/base.html`. Current order: Locations · Registry · People · Faces ·
Speakers → becomes: Locations · Registry · People · Faces · Speakers · Prompts.

---

### 6. "Reset to built-in" behaviour

Activate button on a builtin row reactivates it: `POST /api/knowledge/prompts/{builtin_id}/activate?kb=name`.
No separate reset endpoint is needed. User-created prompts for that key become
inactive but are not deleted — the user retains their variants for later use.

---

## Tests

### `tests/unit/test_stage_prompts.py` — ~15 unit tests

All tests use a real SQLite connection in `tmp_path`; no mocking of DB.

- `seed_stage_prompts` inserts exactly 4 rows (one per key)
- `seed_stage_prompts` is idempotent (second call leaves row count unchanged)
- `load_stage_prompt` returns `default` when no rows exist
- `load_stage_prompt` returns `default` when no active row for the key
- `load_stage_prompt` returns the active body when one exists
- `upsert_stage_prompt` creates a new row
- `upsert_stage_prompt` with same name updates body without adding a row
- `set_active_stage_prompt` marks target active and deactivates others for same key
- `set_active_stage_prompt` raises `ValueError` for unknown `prompt_id`
- `delete_stage_prompt` succeeds for a user-created row
- `delete_stage_prompt` raises `ValueError` for a builtin row
- `delete_stage_prompt` on the active row reactivates the builtin for that key
- `_build_system_prompt` (summarize) with custom base and no focus returns base only
- `_build_system_prompt` with custom base + focus appends the focus line
- `_aggregate_descriptions` with custom instruction uses that instruction string

### `tests/integration/test_prompt_library.py` — ~8 integration tests

All tests run against a real KB in `tmp_path` via the FastAPI test client.

- `GET /knowledge/prompts` returns 200 with all 4 builtins listed
- `POST /api/knowledge/prompts?kb=name` creates a new user prompt (201)
- `PUT /api/knowledge/prompts/{id}?kb=name` updates body (200)
- `POST /api/knowledge/prompts/{id}/activate?kb=name` activates and returns partial (200)
- After activate, `load_stage_prompt` for that key returns the new body
- `DELETE /api/knowledge/prompts/{id}?kb=name` deletes a user prompt (200)
- `DELETE /api/knowledge/prompts/{id}?kb=name` on a builtin returns 400
- After deleting the active user prompt, the builtin for that key becomes active

---

## Files Touched

| File | Change |
|---|---|
| `src/migrations/knowledge/0003_stage_prompts.sql` | New |
| `src/db/kb.py` | Add 6 DB helpers; call `seed_stage_prompts` in `open_kb` |
| `src/stages/describe.py` | Extract `_AGGREGATE_INSTRUCTION`; add `instruction` param to `_aggregate_descriptions`; load prompts in `run_describe`; add `kb_path` param to `run_describe_file` |
| `src/stages/retag.py` | Load `retag/system` before loop; pass to `session.generate` |
| `src/stages/summarize.py` | Extract `_SUMMARIZE_BASE`; add `base` param to `_build_system_prompt`; refactor `_summarize_chunks` to accept system string; load prompt in `run_summarize` |
| `src/cli/quick.py` | Add `--kb` option to `quick_describe`; pass `kb_path` to `run_describe_file` |
| `src/api/ui.py` | Add `GET /knowledge/prompts` page route |
| `src/api/knowledge.py` | Add 4 CRUD endpoints |
| `templates/prompt_library.html` | New |
| `templates/base.html` | Add Prompts nav link |
| `tests/unit/test_stage_prompts.py` | New — ~15 unit tests |
| `tests/integration/test_prompt_library.py` | New — ~8 integration tests |

---

## Acceptance Criteria

1. `stage_prompts` table exists in `knowledge.db` after `kb create`
2. Exactly 4 built-in prompts are seeded (`is_builtin=1, is_active=1`)
3. `load_stage_prompt` returns the active body or the provided default
4. All three LLM stages load their prompt(s) once before the file loop
5. `run_describe_file(path, config, kb_path=some_kb)` uses the active
   `describe/system` prompt from that KB
6. `enrich quick describe --kb ./my_kb/ path/to/files/` uses the KB's active
   describe prompt
7. `/knowledge/prompts` page lists all prompts grouped by stage/key
8. Activate/edit/delete work via HTMX without full page reload
9. Delete on a builtin returns HTTP 400
10. All 1196 existing tests still pass; new total ≥ 1219

---

## Target Test Count

1196 (baseline) + 15 (unit) + 8 (integration) = **1219**

Actual count may vary slightly based on parametrisation decisions during
implementation.
