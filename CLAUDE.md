# Working Agreement — KB Builder

This file governs how Claude Code should approach work on this project. Follow it at the start of every session and every sprint.

## Design Authority

The canonical design documents are:

- `SPEC.md` — full technical reference: schemas, stage behaviour, normalization instruments, suggestion levels, entity tables, field map, review queues, onboarding, sync tracking, outputs
- `VISION.md` — purpose, scope, pipeline summary, two-database design brief, target user, glossary
- `docs/development/ARCHITECTURE.md` — module layout, design principles, API patterns, SQLite conventions

These are the single source of truth for schema, pipeline stages, CLI commands, API patterns, config structure, and all design decisions. Read the relevant sections before implementing any feature. If anything in the codebase contradicts them, raise the conflict before proceeding.

Supporting memory files in the main app's memory store (read when relevant):
- `project_whisper_transcription.md` — Stage 3b (Transcribe) detail
- `project_two_pass_describe_design.md` — Pass 2 (Retag) prompt spec
- `project_standalone_describe_utility.md` — VD.1–VD.4 describe functions (adapt for Stage 3a)

## Pre-Sprint Ritual

Before writing any code for a sprint:

1. **Read** `VISION.md`, `SPEC.md` (relevant sections), `docs/development/ARCHITECTURE.md`, and any sprint file in `docs/development/sprints/active/`.
2. **Read** the sprint entry for the sprint about to start. Confirm the acceptance criteria are clear. If anything is ambiguous, ask before starting.
3. **Identify** which previous sprints this one builds on. Read their memory entries to understand the schema and module state at that point.
4. **Run the test suite** to confirm the current baseline: `python -m pytest tests/ -q`. Record the passing count. Do not start sprint work if existing tests are failing — resolve failures first and confirm with the user.
5. **State** a one-paragraph summary of what the sprint will build and how it connects to previous work. Wait for confirmation before proceeding if anything is uncertain.

## During a Sprint

### Raise critical issues immediately

If a critical problem is discovered mid-sprint — a design inconsistency, a schema conflict, a dependency that is harder than anticipated, or an approach that would require revisiting a previous sprint's work — **stop and raise it immediately**. Do not work around it, defer it, or note it for later. A known problem that compounds across sprints is far more expensive than a short pause to resolve it cleanly.

A critical issue is anything that would require:
- Changing a migration that has already been applied in a previous sprint
- Altering a module interface that other modules depend on
- Contradicting a decision documented in the design authority

### Stay within sprint scope

Do not implement features beyond the sprint's acceptance criteria, even if they seem obvious or easy. If something belongs in a later sprint, note it for that sprint's plan and move on. Scope creep in sprint N costs more than it saves in sprint N+1.

### Follow the architecture

All implementation decisions must follow `docs/development/ARCHITECTURE.md`. Specifically:
- Thin edges: no business logic in `src/cli/` or `src/api/`
- Explicit dependencies: no module-level globals, no implicit imports between siblings
- Named DB functions: no inline SQL in stage modules
- Lazy imports: ML and NLP libraries imported inside functions, never at module level
- API patterns: new endpoints follow Pattern 1, 2, or 3 — no new shapes

If the architecture document does not cover a situation, document the decision and update `docs/development/ARCHITECTURE.md` before proceeding.

### No deferred cleanup

Do not leave TODOs, stubs, or temporary code with a note to fix later. If something cannot be done cleanly within the sprint, raise it rather than shipping it broken. "I'll clean this up" is not an acceptable sprint output state.

## Post-Sprint Verification

A sprint is not complete until all of the following are true:

1. **Tests pass:** Run `python -m pytest tests/ -q`. All tests pass. The count equals or exceeds the sprint target.
2. **New tests exist:** Every new stage function, DB helper, API endpoint, and non-trivial logic path has at least one test. Tests live in `tests/unit/` or `tests/integration/` as appropriate — never in the source tree.
3. **No regressions:** The tests that were passing before the sprint are still passing.
4. **Sprint memory recorded:** Create or update the sprint status memory entry with: what was built, the final test count, and any issues discovered.
5. **Issues surfaced:** If any open questions or risks were discovered during the sprint, list them explicitly before declaring the sprint complete. Do not silently defer them.

## Testing Conventions

- All tests live under `tests/` — never in `src/` or alongside source files
- Unit tests in `tests/unit/` — test individual functions in isolation with no DB or filesystem
- Integration tests in `tests/integration/` — run against real SQLite in `tmp_path` fixtures; no mocking of DB or ExifTool
- GPU/LLM stages (`describe.py`, `transcribe.py`, `retag.py`) are not tested in CI — manual validation only
- The `conftest.py` fixture creates fresh `corpus.db` and `knowledge.db` in `tmp_path` for each test; synthetic media files are generated in `tmp_path` using PIL and ffmpeg — no real files are committed to git
- `tests/fixtures/corpus/` is gitignored; place real files there only for local manual spot-checking
- A stage integration test must cover: happy path, resume-on-restart (interrupt and re-run), and at least one failure mode
- Run `ruff check src/ tests/` alongside pytest — import errors and unused names are build failures

## Communication

- If the sprint plan has an ambiguity that would require a design decision to resolve, ask before implementing — do not guess and proceed
- If the test count comes in significantly below target, explain why before declaring the sprint done
- Keep responses concise during implementation — the user can read diffs; avoid narrating what the code does
