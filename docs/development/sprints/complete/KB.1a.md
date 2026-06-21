# Sprint KB.1a — Ingest + Analyse + Normalization Review API

## Scope

Backend half of KB.1. Delivers: KB lifecycle (create, list), source management (add, list), Stage 0 (Ingest), Stage 0.5 (Analyse), Pattern 1 API (stage run/status/stream), Pattern 2 API (normalise review queue), CLI commands for all of the above, and full test coverage. No HTML templates — those are KB.1b.

After this sprint the tool can walk a file corpus, classify filename/path tokens by pattern type, and expose a complete REST API for the Normalization Review. The API contract is stable before the UI is built against it in KB.1b.

## Builds On

- KB.0: schema (corpus.db + knowledge.db), migrations, config, DAG, CLI shell, API shell, progress + cancel primitives

## Baseline

26 tests passing, ruff clean.

## Deliverables

### New modules
- `src/db/registry.py` — minimal KB registry (open_registry, register_kb, get_kb_path, list_kbs, set_active, get_active_kb_path)
- `src/stages/ingest.py` — Stage 0: stat walk, file_type detection, skip logic, batch upsert, pipeline_checkpoints update
- `src/stages/analyse.py` — Stage 0.5: tokenize, classify, common-prefix strip, depth analysis, cross-source badge, pipeline_checkpoints update
- `src/cli/source.py` — source sub-app (add, list)

### Modified modules
- `src/db/corpus.py` — add named query functions (13 new functions)
- `src/db/kb.py` — add named query functions (7 new functions)
- `src/cli/kb.py` — add kb create, kb list
- `src/cli/pipeline.py` — add ingest, analyse, run
- `src/cli/review.py` — add review normalise (prints URL + opens browser)
- `src/cli/__init__.py` — add source sub-app
- `src/api/pipeline.py` — Pattern 1 for ingest + analyse (run, cancel, status, stream)
- `src/api/review.py` — Pattern 2 for normalise queue (pending, decide, decisions, delete)
- `src/api/sources.py` — sources GET + POST
- `src/api/ui.py` — stub page routes (plain text 200 placeholders, replaced in KB.1b)

## Acceptance Criteria

1. `enrich kb create bc-test` creates `knowledge-bases/bc-test/` with corpus.db, knowledge.db, and a registry.db entry
2. `enrich source add --kb bc-test ./path --type images` adds a row to corpus.db sources table
3. `enrich ingest --kb bc-test` walks source paths and populates files table; second run skips unchanged files
4. `enrich analyse --kb bc-test` populates analyse_tokens with classified tokens
5. `enrich run --kb bc-test` chains ingest → analyse → pauses at normalise_review touchpoint with printed message
6. `enrich review normalise --kb bc-test` prints browser URL without error
7. `GET /api/review/normalise/pending?kb=bc-test` returns paginated token list with total/reviewed counts
8. `POST /api/review/normalise/decide` with action='capture' writes to capture_rules and marks token decided
9. `POST /api/review/normalise/decide` with action='ignore' writes to stoplist (domain scope)
10. `GET /api/review/normalise/decisions` returns composite view across all 4 knowledge.db decision tables
11. `DELETE /api/review/normalise/decisions/{id}` removes the rule and returns token to pending status
12. `python -m pytest tests/ -q` → 52 tests passing; `ruff check src/ tests/` → 0 errors

## Test Targets — 26 new tests

### `tests/unit/test_token_classifier.py` (8 tests)
- `test_classify_6digit_date` — '160929' → ('6digit_numeric', 'date')
- `test_classify_6digit_time` — '094814' → ('6digit_numeric', 'time')
- `test_classify_6digit_ambiguous` — '123456' → ('6digit_numeric', 'unclassified')
- `test_classify_8digit_date` — '20160929' → ('8digit_numeric', 'date')
- `test_classify_sequential` — '001' → ('sequential', 'sequential')
- `test_classify_camelcase` — 'TuckInleted' → ('camelcase', 'compound')
- `test_classify_route_code` — 'BC-5' → ('route_code', 'code')
- `test_tokenize_path_splits_delimiters` — 'BC-Hwy_97C' splits into ['bc', 'hwy', '97c']

### `tests/integration/test_ingest.py` (5 tests)
- `test_ingest_populates_files_table` — 3 images ingested via add_source + run_ingest; files table has 3 rows
- `test_ingest_skips_unchanged_file` — run_ingest twice; second run adds 0 new rows
- `test_ingest_detects_file_type` — .jpg→images, .mp4→video, .mp3→audio; file_type column correct
- `test_ingest_updates_pipeline_checkpoint` — pipeline_checkpoints has stage='ingest' row after run
- `test_ingest_no_sources_is_noop` — no sources in DB; run_ingest exits cleanly, files table empty

### `tests/integration/test_analyse.py` (5 tests)
- `test_analyse_populates_analyse_tokens` — after ingest of named files; analyse_tokens non-empty
- `test_analyse_strips_common_prefix` — files all under /a/b/c/; tokens from prefix do not appear
- `test_analyse_classifies_date_token` — file '160929_clip001.jpg'; token '160929' has semantic_type='date'
- `test_analyse_reruns_are_idempotent` — run twice; token count same, no duplicates
- `test_analyse_updates_pipeline_checkpoint` — pipeline_checkpoints has stage='analyse' row after run

### `tests/integration/test_normalise_review.py` (8 tests)
All via `fastapi.testclient.TestClient` against real DBs in tmp_path. Uses dependency override to inject test KB paths.

- `test_pending_returns_items` — GET /api/review/normalise/pending?kb=x → 200, non-empty items list
- `test_decide_capture_writes_capture_rule` — POST decide action='capture' → capture_rules row written
- `test_decide_ignore_writes_stoplist` — POST decide action='ignore' → stoplist domain row written
- `test_decide_correct_writes_corrections` — POST decide action='correct' → corrections row written
- `test_decide_reject_writes_reject_token` — POST decide action='reject' → reject_tokens row written
- `test_decide_marks_token_decided` — after decide, analyse_tokens row status='decided'
- `test_decisions_list_reflects_all_kinds` — GET decisions → rows from all 4 knowledge.db tables
- `test_delete_decision_reverts_token_to_pending` — DELETE decisions/capture_rules:1 → rule deleted, token pending

## Design Notes

### Registry
`registry.db` lives at the tool root (cwd when `enrich serve` or any CLI command runs). Schema is created inline in `open_registry()` — no migration runner (registry is trivial; one table). Tests override via FastAPI's `dependency_overrides`.

### API KB resolution
All API endpoints that need DB access use a FastAPI `Depends(get_kb_paths)` dependency that:
1. Reads the `kb` query param (or request body field)
2. Opens `registry.db` and resolves name → (corpus_path, kb_path)
3. Returns the tuple to the endpoint handler
For test isolation, `app.dependency_overrides[get_kb_paths]` is set in test fixtures.

### Stage function signatures
All stages follow the established pattern:
```python
def run_ingest(corpus_path: Path, kb_path: Path, config: Config,
               progress: ProgressReporter, cancel_event: threading.Event) -> None
```
Pure helpers (`detect_file_type`, `classify_token`, `tokenize_path`, `detect_common_prefix`) take no DB parameters — tested in isolation in test_token_classifier.py.

### Token classification rules
- 6-digit: YYMMDD range check (MM∈[01..12], DD∈[01..31]) → date; HHMMSS (HH∈[00..23], MM∈[00..59]) → time; else → unclassified
- 8-digit: YYYYMMDD range check → date; else → unclassified  
- Leading `_`+3-4 digits only → sequential
- `[A-Z]{1,4}-\d+` pattern → route_code
- Interior uppercase letter in otherwise mixed-case token → camelcase
- Remaining alphanumeric → word

### Decision composite IDs
The decisions GET endpoint returns `"id": "capture_rules:5"` (table:rowid). The DELETE endpoint splits on `:` to identify which table to delete from and which `analyse_tokens` row to set back to `pending`. The mapping from decision to token uses `analyse_tokens.token` matched against the stored value in each decision table.

## Out of Scope

- HTML templates (KB.1b)
- HTMX + JavaScript (KB.1b)
- Keyboard shortcuts, scroll persistence, progressive polling (KB.1b)
- Entity table seeding scaffold (KB.7)
- LLM-assisted capture rule naming (KB.5+)
- Full KB management: delete, set-active, health (KB.7)
- Source remove / purge (KB.7)
