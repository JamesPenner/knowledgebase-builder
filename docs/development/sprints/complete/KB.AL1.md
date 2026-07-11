# KB.AL1 — Health Page Redesign

**Status:** Complete
**Branch:** `clean-master`
**Baseline:** 1786 tests passing, 2 skipped
**Result:** 1792 tests passing, 2 skipped (+6 net)
**Preceding sprint:** KB.AK1 (Corpus File Browser, 1786 tests)

## Goal

Source concept: `docs/development/sprints/planned/UI_REDESIGN_CONCEPT.md` §4 —
the health page mixed genuine system blockers (tools missing, models
unconfigured) with informational corpus-coverage counts (files ingested,
vocabulary size), so a coverage gap like "spaCy not installed" and a
`0`-vocabulary KB looked equally alarming. Split into **System Health**
(pass/fail, fix guidance) and **Corpus Coverage** (plain numbers, no
red/warning framing) so coverage gaps read as expected state, not failure.

## Investigation Finding — Pre-existing Display Bug

Both `src/api/ui.py`'s `health_page()` and `src/cli/kb.py`'s `kb_health`
command grouped `HealthCheck`s by hardcoded `id` membership sets written at
KB.P10 (16 checks). `run_checks()` has grown to 28 checks across five later
sprints (KB.P21–P25, KB.Y1) — `audio_model`, `aesthetic_nima`,
`aesthetic_clip`, `face_detection_model`, `face_embedding_model`,
`voice_model`, `diarization_model`, `whisper_cli`, `geolocate_data`,
`privacy_zones`, `validation_freshness`, and `location_register` matched no
group's `id` set in either function and were **silently dropped** from both
the web page and the CLI output — 12 of 28 checks were invisible. This
sprint's severity-based regrouping fixes it as a side effect of removing the
hardcoded lists, and a regression test (`test_health_page_all_checks_rendered`)
now guards against it recurring.

## Design Decision — Severity as the Split, Not a New Field

`HealthCheck.severity` (`error`/`warning`/`info`) already encodes almost
exactly the System-Health/Corpus-Coverage distinction the concept doc wants:
`error`/`warning` checks are things to fix; `info` checks are counts. The one
mismatch: the five scaffold-file checks (`library.yaml`,
`reference/ExifTool_Config`, `reference/dates.yaml`,
`reference/derive_rules.yaml`, `reference/taxonomy.yaml`) were `severity="info"`
even though a missing scaffold file blocks write-back/catalogue
compatibility — a genuine blocker per the concept doc's own example
("scaffold files missing"). Bumped those five to `severity="warning"`. No
other check's severity needed to change; `validation_freshness` and
`location_register` already flip `info`→`warning` dynamically when there's
an actual problem, which turned out to already match the target framing.

This makes the split mechanical and self-maintaining: `split_checks()` in
`src/health.py` buckets by severity, so a new check added to `run_checks()`
in a future sprint lands in the right section automatically as long as its
severity is set correctly — no group-membership list to remember to update
(the exact thing that caused the display bug above).

## Scope Decision — Link, Don't Embed (confirmed with user)

Corpus Coverage renders only the existing `info`-severity `HealthCheck`s
(source/file counts, vocabulary size, FOCUS string, unreviewed EXIF fields,
validation freshness, location register) plus two links —
`/corpus-stats?kb=` (stage-by-stage coverage percentages, already built in
KB.P1/P3) and `/knowledge/people?kb=` (face/voice centroid reliability,
already built in KB.AJ2's `get_centroid_quality()`). Considered pulling a
condensed centroid-quality summary directly into the Corpus Coverage section,
but rejected it: `src/health.py` would then need to import
`get_centroid_quality()` and know its threshold parameters
(`face_min_clusters`, `face_min_similarity`, etc.), which today are owned by
the people/faces review module. Linking keeps each metric's rendering and
computation in one place and keeps `health.py`'s dependencies limited to
`config`/`corpus_conn`/`kb_conn`/`kb_folder`, as before.

## What Was Built

### `src/health.py`

- `split_checks(checks) -> tuple[list[HealthCheck], list[HealthCheck]]` —
  buckets by severity (`error`/`warning` → system, `info` → coverage).
- Five scaffold-file checks (`_check_yaml_file`, `_check_exiftool_config`)
  changed from `severity="info"` to `severity="warning"`.

### `src/api/ui.py`

- `health_page()` now calls `split_checks()` and passes `system_checks`/
  `coverage_checks` to the template instead of building four hardcoded
  `{"label": ..., "checks": [...]}` groups.

### `src/cli/kb.py`

- `kb_health` CLI command mirrors the same two-section split via
  `split_checks()`, replacing the same stale hardcoded `_GROUPS`/`_GROUP_IDS`
  lists.

### `templates/health.html`

Rewritten: `System Health` section (dot indicators, fix guidance, same
markup/CSS classes as before) and `Corpus Coverage` section (new
`.coverage-row` markup — label + detail only, no dot, no fix code block) with
a subtitle linking to `/corpus-stats` and `/knowledge/people`.

### `static/css/main.css`

- Removed `.health-group-title` and `.health-dot--info` (dead — no longer
  referenced by any template after the section rewrite).
- Added `.section-subtitle` (reused nowhere else yet, but generic) and
  `.coverage-dashboard`/`.coverage-row`/`.coverage-label`/`.coverage-detail`.

### `VISION.md`

Added a "Keep sibling apps composable, not just decoupled" principle after
the "Shared library before shared service" paragraph, capturing a design
discussion from this session: future sibling apps (people/face/voice
management, location management, etc.) should stay mountable as ASGI
routers by construction, so an "Integrate" capability that merges N of them
into one process is a composition operation rather than a rewrite — a
standing constraint to hold, not a feature to build now (no second sibling
app exists yet).

### `SPEC.md`

"Health checker" section rewritten to describe the System Health / Corpus
Coverage split and the severity-based mechanism, replacing a stale 11-row
table that hadn't tracked the check list since KB.P10 (16 checks; actual is
28). Points at `src/health.py::run_checks()` as the living source of truth
for the full check list rather than re-enumerating it in the doc.

## Files Touched

| File | Change |
|---|---|
| `src/health.py` | New `split_checks()`; 5 scaffold checks `info`→`warning` |
| `src/api/ui.py` | `health_page()` uses `split_checks()` |
| `src/cli/kb.py` | `kb health` CLI uses `split_checks()` |
| `templates/health.html` | Two-section rewrite |
| `static/css/main.css` | Coverage dashboard CSS; removed 2 dead rules |
| `VISION.md` | New composability principle |
| `SPEC.md` | Health checker section rewritten |
| `docs/development/sprints/planned/UI_REDESIGN_CONCEPT.md` | §4 marked done |
| `tests/unit/test_health.py` | Scaffold severity assertions updated; +4 `split_checks` tests |
| `tests/integration/test_ui.py` | Group-label test replaced with 3 tests (sections, all-checks-rendered regression, links) |
| `tests/integration/test_kb_management.py` | CLI group-label assertions updated |

## Test Coverage

+6 net tests: `split_checks()` unit coverage (bucketing, order preservation,
empty input, real `run_checks()` output — asserting scaffold checks land in
System Health and KB-state checks land in Corpus Coverage); integration
coverage for the two-section page render, the all-checks-rendered regression
(guards against the display bug this sprint found), the Stats/People links,
and updated CLI output assertions.

## Manual Verification

Ran the dev server (`enrich serve`) against the real `test-run` KB (27,136
files). Confirmed via direct HTML fetch: System Health renders all 20
error/warning checks with correct dots (2 tool checks green, 3 model-install
warnings genuinely absent on this machine — spaCy, Resemblyzer, pyannote —
1 scaffold warning for a genuinely-missing `ExifTool_Config`); Corpus
Coverage renders all 8 info checks as plain label/value rows with no dots;
both `/corpus-stats?kb=test-run` and `/knowledge/people?kb=test-run` links
present and correctly parameterised. This is also where the pre-existing
display bug was directly confirmed — before this sprint's fix, 12 of these
28 checks (including the Natural Earth / model-install ones visible above)
would not have appeared on the page at all.

## Issues Surfaced

- `SPEC.md`'s health-checker table had drifted significantly out of sync
  with `src/health.py` (documented 11 checks against an actual 28, and
  hadn't been updated across five sprints that added checks). Rewrote the
  section to point at the code as source of truth instead of re-listing
  every check, to avoid the same drift recurring — but this is a pattern
  worth watching for in other SPEC.md sections that mirror fast-growing code
  (e.g. classify rules, health checks, quality metrics).
- `tests/unit/test_health.py::test_run_checks_returns_24` is misnamed (it
  asserts `len(checks) == 28`) — predates this sprint, left as-is since
  renaming it wasn't in scope.

## Out of Scope — Deferred

- Embedding a condensed face/voice centroid-reliability summary directly on
  the Health page (see Scope Decision above) — linked to `/knowledge/people`
  instead.
- Remaining `UI_REDESIGN_CONCEPT.md` items: Vocabulary Review Improvements,
  Export Page Framing.
