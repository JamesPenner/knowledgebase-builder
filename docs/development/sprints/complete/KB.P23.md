# Sprint KB.P23 — File Validation Pipeline

## Goal

Add an `enrich corpus validate` command that checks every ingested file against the corpus record and reports its status. Users running long-lived corpora need to know when files have moved, been modified, or gone missing since the last ingest — no stage currently surfaces this.

## Baseline

868 tests (committed at KB.P22).

## Scope

No changes to existing pipeline stages or the pipeline DAG. Validation is a standalone corpus-maintenance operation: it reads the corpus, checks the filesystem, and records results. It does not modify file metadata or trigger write-back.

## Deliverables

### New files

- `src/migrations/corpus/0015_validation.sql` — two tables:
  - `validation_runs (id PK, run_at, files_checked, ok_count, changed_count, moved_count, missing_count)`
  - `validation_results (id PK, run_id FK, file_id FK, status TEXT, detail TEXT)` — status is one of `ok`, `changed`, `moved`, `missing`
- `src/stages/validate.py` — `run_validate(corpus_path, kb_folder, progress, cancel_event, export=False)` function
- `src/cli/validate.py` — `enrich corpus validate [--kb NAME] [--export]` command
- `tests/unit/test_validate_unit.py`
- `tests/integration/test_validate_integration.py`

### Modified files

- `src/db/corpus.py` — DB helpers: `insert_validation_run`, `insert_validation_result`, `get_latest_validation_summary`, `get_validation_results_for_export`
- `src/cli/__init__.py` — wire `enrich corpus validate`
- `src/health.py` — `_check_validation_freshness(corpus_conn)`: warns if the most recent run found `changed` or `missing` files, or if no validation run exists at all; 23 total checks
- `src/stages/export.py` — `_write_validation_report()` writes `export/validation_report.csv` (path, status, detail, checked_at); called when `--export` flag is set or as part of a full export run
- `tests/integration/test_schema.py` — add `validation_runs` and `validation_results` to `_CORPUS_TABLES`
- `tests/unit/test_health.py` — update check count to 23; add tests for `_check_validation_freshness`

## Status Classification Logic

For each file in `files` table:

| Condition | Status | Detail |
|---|---|---|
| File exists at recorded path AND current SHA-256 matches stored hash | `ok` | — |
| File exists at recorded path BUT SHA-256 differs | `changed` | current hash |
| File not found at path BUT matching SHA-256 found at another path in corpus | `moved` | new path |
| File not found at path AND no matching hash anywhere | `missing` | — |

The `moved` check queries `file_hashes` for the stored hash, then checks if any *other* file in `files` now has that hash at a path that exists on disk. This handles simple renames and moves without a full filesystem scan.

## CLI

```
enrich corpus validate --kb <name>           # check all files, store results
enrich corpus validate --kb <name> --export  # also write export/validation_report.csv
```

Output: progress bar during run; summary line on completion (`847 ok, 3 changed, 1 moved, 12 missing`).

## Export CSV

`export/validation_report.csv` columns: `path, status, detail, checked_at`

Only includes files with status other than `ok` by default (to keep the file useful). Full output if a `--all` flag is added in a future sprint.

## Health Check

`_check_validation_freshness(corpus_conn)`:
- No validation run ever: `ok=True`, severity `info`, detail "No validation run recorded — consider running `enrich corpus validate`"
- Most recent run has `changed > 0` or `missing > 0`: `ok=False`, severity `warning`, detail lists counts
- Most recent run is clean: `ok=True`, detail shows run date and file count

## Test Targets

- Unit: status classification logic for each of the four states, DB helpers, export row generation — ~15 tests
- Integration: happy path (all ok), changed file (hash mismatch), moved file (found at new path), missing file, export CSV, schema migration, health check — ~10 tests
- Health check unit tests: no run, clean run, dirty run — 3 tests

**Target: 868 → 900+ tests (+32)**

## Acceptance Criteria

1. `enrich corpus validate --kb <name>` runs without error on a clean corpus and reports all files as `ok`
2. A file whose content has changed since ingest is classified as `changed`
3. A file renamed/moved on disk (but still in the corpus path list) is classified as `moved`
4. A file deleted from disk is classified as `missing`
5. Results are stored in `validation_runs` / `validation_results` and queryable
6. `--export` writes `export/validation_report.csv` with non-ok files only
7. Health check warns when the most recent run contains changed or missing files
8. All 900+ tests pass; ruff clean
