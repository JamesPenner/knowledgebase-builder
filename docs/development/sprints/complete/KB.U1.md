# Sprint KB.U1 — Source Management & File Sets

## Goal

Close the biggest workflow gap in the web UI: a new user can currently only
define corpus sources via the CLI. This sprint surfaces source management
directly in the workbench, adds per-source filter criteria that narrow what
gets ingested, introduces named file sets for repeatable work (e.g. a fixed
test batch used across multiple sessions), and adds a `+ New KB` entry point
in the nav.

---

## Acceptance Criteria

### AC1 — corpus.db migration 0020

Three schema changes, all in corpus.db:

**`sources` table — add `filters_json` column**
```sql
ALTER TABLE sources ADD COLUMN filters_json TEXT NOT NULL DEFAULT '{}';
```
Stores filter criteria applied during the ingest directory walk. The existing
`file_type` and `recursive` columns remain as first-class columns; `filters_json`
holds additional filter types that can be extended without future migrations.

Initial recognised keys: `glob` (str, fnmatch pattern), `count_limit` (int).

**New table `file_sets`**
```sql
CREATE TABLE file_sets (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name)
);
```

**New table `file_set_members`**
```sql
CREATE TABLE file_set_members (
    set_id  INTEGER NOT NULL REFERENCES file_sets(id) ON DELETE CASCADE,
    file_id INTEGER NOT NULL REFERENCES files(id)     ON DELETE CASCADE,
    PRIMARY KEY (set_id, file_id)
);
```

---

### AC2 — `apply_source_filters` helper in `src/stages/ingest.py`

```python
def apply_source_filters(files: list[Path], filters: dict) -> list[Path]:
```

Applies filter criteria in order:
1. `glob` — fnmatch pattern matched against `file.name` only (not full path)
2. `count_limit` — truncate list to first N after glob filter

All filter logic lives here. Both `run_ingest` and the preview endpoint call
this function. New filter types are added here and nowhere else.

`run_ingest` updated to:
- Load `filters_json` per source (JSON-decoded, default `{}` on decode error)
- Call `apply_source_filters(candidates, filters)` after building the file list
  for each source, before the upsert loop

---

### AC3 — Source DB helpers in `src/db/corpus.py`

**Update `add_source` signature:**
```python
def add_source(
    conn, path: str, file_type: str = "all",
    recursive: bool = True, filters_json: dict | None = None
) -> int:
```
Serialises `filters_json` to JSON string on insert. Existing callers pass no
`filters_json` and get `'{}'` stored.

**New `remove_source`:**
```python
def remove_source(conn, source_id: int, cascade: bool = False) -> int:
```
- If `cascade=True`: deletes all rows from `files WHERE source_id = ?` inside
  a transaction. FK `ON DELETE CASCADE` on all dependent tables handles the
  downstream cleanup. Returns count of files deleted.
- If `cascade=False`: sets `removed_at = datetime('now')` on the source row
  only; does not touch files. Returns 0.

**Verify FK cascades before implementing:** confirm that all tables with
`file_id` FK (descriptions, file_quality, file_face_regions, file_voice_embeddings,
file_geolabels, file_gps_masks, file_validation, temporal_derived_fields, etc.)
were created with `ON DELETE CASCADE`. If any are missing, add them via a
migration rather than working around it.

**New file-set helpers:**
```python
def create_file_set(conn, name: str, description: str, file_ids: list[int]) -> int
def get_file_sets(conn) -> list[sqlite3.Row]          # id, name, description, file_count, created_at
def delete_file_set(conn, set_id: int) -> None
def resolve_set_file_ids(conn, set_id: int) -> frozenset[int]
```

`get_file_sets` uses a subquery for file_count:
```sql
SELECT fs.*, (SELECT COUNT(*) FROM file_set_members WHERE set_id = fs.id) AS file_count
FROM file_sets fs ORDER BY fs.created_at DESC
```

---

### AC4 — `set_id` scope on pending-file queries

The six `get_pending_*` functions extended in KB.T2 gain a `set_id` optional
param following the same `(? IS NULL OR ...)` pattern already established:

```sql
AND (? IS NULL OR f.id IN (SELECT file_id FROM file_set_members WHERE set_id = ?))
```

Functions to update (pass `set_id` as two bind params for the IS NULL pattern):
- `get_pending_describe_files`
- `get_pending_transcribe_files`
- `get_pending_quality_files`
- `get_pending_aesthetic_files`
- `get_pending_summarize_files`
- `get_pending_retag_files`

The corresponding stage functions (`run_describe`, `run_transcribe`, etc.) gain
`set_id: int | None = None` as a keyword argument and forward it to the
pending-file query.

---

### AC5 — Source and set API endpoints

All new endpoints go in `src/api/kb.py` following Pattern 1 (data operations,
no background tasks):

**`POST /api/kb/{name}/sources`**
Body: `{ "path": str, "file_type": str, "recursive": bool, "filters": dict }`
Calls `add_source`. Returns `{ "id": int, "path": str }`.
Validates that the path exists on disk before inserting; returns 422 if not found.

**`DELETE /api/kb/{name}/sources/{source_id}`**
Query param: `cascade: bool = False`
Calls `remove_source`. Returns `{ "deleted_files": int }`.

**`POST /api/kb/{name}/sources/preview`**
Body: `{ "path": str, "file_type": str, "recursive": bool, "filters": dict }`
Runs the ingest directory walk (same logic as `run_ingest`) with `apply_source_filters`
applied. No DB writes. Returns:
```json
{ "total": int, "by_type": { "images": int, "video": int, "audio": int } }
```
Returns 422 if path does not exist.

**`GET /api/kb/{name}/sets`**
Returns list of `{ id, name, description, file_count, created_at }`.

**`POST /api/kb/{name}/sets`**
Body: `{ "name": str, "description": str, "scope": { ... } }`
`scope` uses the same shape as the existing scope selector state
(`scope_mode`, `source_id`, `file_type`, `set_id`). The handler resolves the
scope to a list of file IDs from the corpus, then calls `create_file_set`.
Returns `{ "id": int, "file_count": int }`.
Returns 422 if name already exists.

**`DELETE /api/kb/{name}/sets/{set_id}`**
Calls `delete_file_set`. Returns `{ "status": "deleted" }`.

---

### AC6 — `RunRequest` and runner scope wiring

`RunRequest` in `src/api/pipeline.py` gains `set_id: int | None = None`.

`_make_stage_routes` extracts `set_id` from the request and includes it in the
`scope` dict passed to runners.

The six stage runners that support source_id/file_type scope (`_describe_runner`,
`_transcribe_runner`, `_quality_runner`, `_aesthetic_runner`, `_retag_runner`,
`_summarize_runner`) forward `set_id` to the stage function. All other runners
accept and ignore it via `**_`.

`WB.getScope()` in `workbench.js` gains `set_id` extraction from the By set
secondary dropdown. `_buildBody` in `pipeline.js` spreads it into the request.

---

### AC7 — Ingest row source management panel

When the ingest stage row is expanded (▸ toggle), the help row renders a full
source management panel via HTMX partial (`GET /api/kb/{name}/sources/panel`).

**Panel structure:**

*Sources section*
- Table: path, file type, filters summary (e.g. "glob: 2024-*, limit: 100"),
  file count, last ingested at, Remove button
- Remove triggers a confirmation inline ("Remove source and delete N files?" with
  Confirm / Cancel); on confirm, `DELETE /api/kb/{name}/sources/{id}?cascade=true`
  then re-renders the partial
- "Add source" inline form below the table:
  - Path input (text)
  - File type select (All / Images / Video / Audio)
  - Recursive checkbox (default: checked)
  - Glob pattern input (optional, placeholder: e.g. `2024-*`)
  - Count limit input (optional number, placeholder: e.g. `100`)
  - "Preview" button — calls preview endpoint, shows "Found: 280 images, 62 video"
    inline without submitting
  - "Add" button — `POST /api/kb/{name}/sources`, re-renders partial on success

*Saved sets section*
- Table: name, description, file count, created at, Delete button
- "Save current scope as set" button below the table — opens an inline name/description
  form; on submit calls `POST /api/kb/{name}/sets` with the current scope state
  from `window.KB_SCOPE`; re-renders partial on success

**Onboarding state:** if `GET /api/kb/{name}/sources` returns an empty list, the
ingest row renders with the help row pre-expanded (no user click needed) and a
"Add your first source to get started" prompt above the add form.

---

### AC8 — `By set` scope mode

Scope selector in `pipeline.html` gains a "By set" option.

A secondary `<select id="scope-set">` (hidden unless mode=by_set) is populated
from `window.KB_SETS` injected by the server alongside `window.KB_SOURCES`.

`src/api/ui.py` `pipeline_page()` fetches sets via `get_file_sets` and passes
them as `sets` to the template context.

`WB.onScopeChange()` shows/hides `#scope-set` and updates the scope summary:
"Processing: 12 files from set 'vacation-2024'".

---

### AC9 — `+ New KB` button and creation form

**Nav change (`templates/base.html`):**
A `+` button is added adjacent to `#kb-switcher`. It is always visible (not
conditional on a KB being active), so users can create a KB from any page.
On click, navigates to `/kb/new`.

**`GET /kb/new` — creation page (`src/api/ui.py` + `templates/kb_new.html`):**
- KB name input (text, required, validated: alphanumeric + hyphens/underscores)
- Template select (only "general-media" for now)
- Initial source section: same path/file_type/recursive/glob/count_limit form
  fields as the ingest panel; includes a Preview button
- Create button

On submit (`POST /kb/new`):
1. Call existing KB create API logic (or inline: `create_kb(name, template)`)
2. If source path provided: call `add_source` on the new corpus
3. Redirect to `/pipeline?kb={name}`

The creation form is a standard HTML form POST, not HTMX — a full page submit
is fine here since it's a one-time action.

---

## New Files

| File | Purpose |
|---|---|
| `src/migrations/corpus/0020_source_filters_and_sets.sql` | AC1 schema changes |
| `templates/kb_new.html` | New KB creation form |
| `tests/unit/test_source_filters.py` | AC2 filter helper unit tests |
| `tests/unit/test_file_sets.py` | AC3 file-set DB helper unit tests |
| `tests/integration/test_source_api.py` | AC5 source endpoints integration tests |
| `tests/integration/test_sets_api.py` | AC5 set endpoints integration tests |
| `tests/integration/test_kb_creation_ui.py` | AC9 new KB form integration test |

---

## Files Modified

| File | Change |
|---|---|
| `src/db/corpus.py` | `add_source` updated; `remove_source`, set helpers added; set_id on 6 `get_pending_*` |
| `src/stages/ingest.py` | `apply_source_filters` helper; `run_ingest` loads and applies filters_json |
| `src/stages/describe.py` | `run_describe` gains `set_id` kwarg |
| `src/stages/transcribe.py` | `run_transcribe` gains `set_id` kwarg |
| `src/stages/quality.py` | `run_quality` gains `set_id` kwarg |
| `src/stages/aesthetic.py` | `run_aesthetic` gains `set_id` kwarg |
| `src/stages/summarize.py` | `run_summarize` gains `set_id` kwarg |
| `src/stages/retag.py` | `run_retag` gains `set_id` kwarg |
| `src/api/kb.py` | 5 new source/set endpoints |
| `src/api/pipeline.py` | `RunRequest` gains `set_id`; runners forward it |
| `src/api/ui.py` | `pipeline_page` passes `sets`; add `kb_new_page` and `kb_new_submit` |
| `templates/base.html` | `+` button adjacent to `#kb-switcher` |
| `templates/pipeline.html` | "By set" scope option; `window.KB_SETS` injection |
| `static/js/workbench.js` | `set_id` in `getScope()`; sets dropdown population; onboarding state |
| `static/js/pipeline.js` | `_buildBody` spreads `set_id` |
| `static/css/main.css` | Source panel, set panel, preview result, onboarding prompt styles |
| `tests/integration/test_schema.py` | Assert `file_sets`, `file_set_members` tables and `filters_json` column |

---

## Test Target

**Baseline:** 1275 passing  
**Target:** ≥ 1330 passing (+55 net)

Test breakdown:
- `apply_source_filters` unit tests: glob matching, count_limit, combined, empty
  filters no-op, unrecognised key ignored (~8)
- File-set DB helper unit tests: create, list, delete, resolve_set_file_ids,
  duplicate name rejected (~8)
- `remove_source` DB helper: soft delete, cascade delete file count (~4)
- `add_source` with filters_json: roundtrip serialisation (~3)
- Source API integration: add (valid path), add (invalid path → 422), preview
  (counts correct), remove soft, remove cascade (~10)
- Set API integration: create from scope, list, delete, duplicate name → 422 (~8)
- `get_pending_*` set_id filter: each of the 6 functions with a set that includes
  and excludes files (~6)
- Stage kwarg forwarding: set_id reaches get_pending_* call (~4)
- New KB form: GET renders, POST creates KB and redirects, POST with source adds
  source (~4)
- Schema test update: 2 new tables + column (~2)

---

## Decisions & Notes

- `filters_json` is the extension point for all future filter types (min/max file
  size, date range, filename regex, exclude patterns, minimum resolution). Adding
  a new filter is: one entry in `apply_source_filters`, one field in the UI form.
  No migration needed.
- Face, voice, diarize, hash, and other stages not in the KB.T2 scope set do not
  gain `set_id` in this sprint. Extend in a follow-up if the need arises.
- Set creation from scope resolves file IDs at save time (a snapshot), not a
  dynamic query. A set is a fixed list of files, not a saved filter. This means
  a set does not automatically grow when new files are ingested.
- The `+` button navigates to `/kb/new` directly; no modal. Modal would require
  JS coordination with the switcher; a dedicated page is simpler and allows the
  preview button to work without inline JS complexity.
- Source preview runs in the request handler (synchronous), not a background task.
  Directory walks on local filesystems are fast enough for the count-only preview.
  Very large sources (100k+ files) may be slow; acceptable for v1.
- `removed_at` soft-delete on sources (cascade=False) is preserved for compatibility
  with existing `get_sources` queries that already filter by `removed_at IS NULL`.
