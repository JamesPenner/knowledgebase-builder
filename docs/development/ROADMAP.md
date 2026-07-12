# KB Builder — Development Roadmap

## Core Philosophy

KB Builder is a **knowledge-building workbench**, not a catalogue manager. Its
primary output is a reusable knowledgebase (vocabulary, entity registries,
people/voice centroids, classification rules) that can be applied to a full
media catalogue by a downstream tool. Enriching and writing metadata back to
files is a secondary capability. Selective, partial processing of a corpus is a
feature, not a deficiency.

See `memory/project_core_philosophy.md` for the full statement.

---

## Current State

- **Branch:** `clean-master`
- **Tests:** 1865 passing, 2 skipped
- **Last completed sprint:** KB.AM3 (Knowledge Settings: UI — collapsible Knowledge Settings panel on `/pipeline` with People/Places/Dates toggles and a Dates & Events calendar-rule enable-list; `pipeline_page()`'s per-stage state gains a gating-aware `skipped`/`partial_note` layer sitting alongside the existing done/ready/blocked dependency state, rendering "Skipped — {Category} disabled" badges and a "Partial — Dates disabled" note on `classify`; new `GET /api/kb/{name}/settings/panel` and `PATCH /api/kb/{name}/classify-rules/{id}` endpoints, plus `GET /pipeline/groups` for reload-free re-rendering after a toggle; +12 net tests)
- **Next planned sprint:** none queued — see Planned Concepts below for unscheduled UI/UX work

---

## Completed Sprints — Backend Refactoring (S-series)

### KB.S1 — LLMSession ✓
**Status:** Complete  
**Document:** `sprints/complete/KB.S1.md`  
**Scope:** `src/llm/` with `TextSession` and `VisionSession` context managers.
Fixed retag.py hardcoded llama2 template bug. Centralised VRAM release via
`gc.collect()`. Wired `deep_seek`/`deep_seek_max_iter` retry for the first time.
**Result:** 1128 tests (+12 net)

### KB.S2 — AudioTrack ✓
**Status:** Complete  
**Document:** `sprints/complete/KB.S2.md`  
**Scope:** `src/media/audiotrack.py` with `AudioTrack`, `AudioProfile`, and
`prepare_audio()` context manager. VAD (RMS gate), clipping detection,
optional normalisation, `has_speech` persisted to `files`. Removed
`_extract_audio()` from transcribe; replaced librosa direct-load in voice/diarize.
Config gains `vad_silence_threshold` and `audio_profile`. Migration 0019.
**Notes:** Sprint plan used `corpus_files` table name — actual table is `files`;
fixed. Sprint plan used `-to` for segment end — replaced with `-t` (duration)
to correctly handle input-seek + output-time interaction.
**Result:** 1157 tests (+29 net)

### KB.S3 — FrameSet ✓
**Status:** Complete  
**Document:** `sprints/complete/KB.S3.md`  
**Scope:** `src/media/frameset.py` with `Frame`, `FrameSet`, `VisualProfile`
dataclasses and `prepare_visual()`. Consistent quality filtering across
describe, face, and quality stages. Correct EXIF transpose. Decompression bomb
guard. Quality gate guarantee (always ≥1 frame). pHash scene dedup.
Config gains `visual_profile`. `_describe_frame`/`_aggregate_descriptions` moved
from `video.py` to `describe.py`. `describe_video()` removed.
Touches: `describe.py`, `face.py`, `quality.py`, `hash.py` (video path only).
**Notes:** `detect_faces`/`embed_face` signatures changed from `Path` to `bytes`
(internal-only callers). Quality/hash stages use `frames + rejected` to include
all frames for metric aggregation. pHash test required checkerboard vs. solid
(solid black + solid white have Hamming distance = 1, not diverse).
**Result:** 1175 tests (+18 net)

---

## Completed Sprints — Backend Refactoring (S-series, continued)

### KB.S4 — FileContext ✓
**Status:** Complete  
**Document:** `sprints/complete/KB.S4.md`  
**Scope:** `src/text/context.py` with `FileContext` dataclass (13 fields incl.
`summary_text`) and `build_file_context()`. Replaced `_assemble_context` in
summarize, `_get_file_context` in describe, and per-file inline queries in
suggest (Level A + C) and retag. Added `base_prompt` parameter to
`_build_describe_prompt` for Prompt Library compatibility. Added 9 named query
functions to corpus.py/kb.py. New `_build_file_text` helper in suggest.py
for testable text pool assembly.
**Notes:** `file_geolabels.method` is NOT NULL — seed test required the column.
Existing `test_retag_build_prompt_no_llama2_template` used old 4-arg signature
— updated to use `FileContext`.
**Result:** 1196 tests (+21 net)

---

### KB.S5 — Prompt Library ✓
**Status:** Complete  
**Document:** `sprints/complete/KB.S5.md`  
**Scope:** Per-KB prompt library in `knowledge.db`. `stage_prompts` table seeded
with 4 built-in prompts at KB creation. `load_stage_prompt()` helper; all three
LLM stages (Describe, Retag, Summarize) load their active prompt once before
the per-file loop. `_aggregate_descriptions` and `_build_system_prompt` accept
prompt override parameters. `run_describe_file` (quick-describe) gains optional
`kb_path` to load the KB's active describe prompt. `enrich quick describe --kb`
CLI flag. `/knowledge/prompts` page + 4 CRUD API endpoints for create/update/
activate/delete. "Prompts" nav link in Knowledge section.
**Notes:** Knowledge router is at `/api/knowledge/` (not `/api/kb/{name}/`);
template and test URLs updated accordingly. Schema test updated for
`stage_prompts` table.
**Result:** 1224 tests (+28 net)

---

## Planned Sprints — Knowledge Settings (AM-series)

**Concept doc:** `sprints/planned/KNOWLEDGE_SETTINGS_CONCEPT.md` — People/
Places/Dates domain toggles that gate which pipeline functions run and which
already-derived content surfaces downstream, without any structural schema
change. Design session (2026-07-11) surveyed the codebase for cross-domain
entwinement (e.g. life events = People × Dates) and found two chokepoints —
`build_file_context()` and `export.py::_write_search_text` — that mix all
three domains with no filtering today; these are the highest-risk part of
the feature and get their own isolated sprint.

### KB.AM1 — Schema & Gating Engine ✓
**Status:** Complete
**Document:** `sprints/complete/KB.AM1.md`
**Scope:** `knowledge_settings` table (migration 0010), `src/pipeline/knowledge_gates.py`
(`STAGE_REQUIRES`, `TAG_CATEGORY_REQUIRES`, `report_stage_skipped`), early-skip
gating placed before config/model validation in all 8 gated stages, in-stage
filtering for `entity_match` and `classify`, settings API + CLI. No UI.
**Result:** 1832 tests (+35 net)

### KB.AM2 — Context & Export Filtering ✓
**Status:** Complete
**Document:** `sprints/complete/KB.AM2.md`
**Scope:** `build_file_context()` gains `enabled_categories`; filters
`entity_names` (by table), `metadata_location`, transcript speaker labels,
and `derived_tags` (by category). `metadata_date` is deliberately **not**
suppressed regardless of the Dates toggle. `export.py::_write_search_text`
consolidated onto the same shared filter helper instead of its own bespoke
query. Isolated from `KB.AM1` — touches five existing LLM-stage call sites.
**Result:** 1853 tests (+21 net)

### KB.AM3 — Settings UI ✓
**Status:** Complete
**Document:** `sprints/complete/KB.AM3.md`
**Scope:** Collapsible Knowledge Settings panel on `/pipeline` (People/Places/Dates
toggles, Dates & Events expansion into a minimal calendar-rule enable-list —
no full Classify Rules manager, per the pre-sprint review finding), cascading
"Skipped — {Category} disabled" / "Partial — Dates disabled" badges on
gated stage rows.
**Result:** 1865 tests (+12 net)

---

## Planned Concepts — UI/UX Redesign (T-series, not yet sprint-planned)

These require a design session before sprint planning. Concept documents
capture decisions made; sprint plans will be written immediately before
implementation.

### Pipeline Workbench Redesign
**Document:** `sprints/planned/UI_REDESIGN_CONCEPT.md`  
Stage grouping by dependency, file scope selector (Resume / Re-run / New files
/ By source / By type), multi-stage selection with auto-resolved dependencies,
inline help per stage, review touchpoints shown as gates not run buttons,
browser-side scheduling.

### Review UI Redesign — Face and Voice
**Document:** `sprints/planned/UI_REDESIGN_CONCEPT.md`  
Shift from "assign all clusters" to "build a reliable centroid." Per-person
centroid confidence and sample count. Highest-value clusters surfaced first.
Good-enough threshold indicator.

### Health Page Redesign
**Status:** Done — `KB.AL1` (see `docs/development/sprints/complete/KB.AL1.md`).
**Document:** `sprints/planned/UI_REDESIGN_CONCEPT.md`  
Split into System Health (genuine problems: missing tools, schema errors) and
Corpus Coverage (informational: % processed, unassigned clusters). Coverage
gaps are expected and should not look like failures.

### Navigation Restructure
**Document:** `sprints/planned/UI_REDESIGN_CONCEPT.md`  
Consolidate nav into: Build / Review / Knowledge / Corpus.

### Corpus File Browser
**Status:** Done — `KB.AK1` (see `docs/development/sprints/complete/KB.AK1.md`).
**Document:** `sprints/planned/UI_REDESIGN_CONCEPT.md`  
Text-only first (filename, type, source, processing state). Enables
"select specific files for focused pipeline run" workflow. Thumbnails deferred
until opportunistic frame cache (below) is evaluated. Implemented as
filter-based scope handoff, not row-selection — see §5 of the concept doc for
why arbitrary multi-select was dropped from scope this sprint.

---

## Deferred Items

These are documented concepts but are not scheduled. Each has a reason for
deferral noted.

| Item | Document | Deferred Until |
|---|---|---|
| Opportunistic frame cache (Option B) | `FRAMESET_CONCEPT.md` | Text-only file browser built and thumbnail need validated |
| `prepare_file()` combined entry point | `FRAMESET_CONCEPT.md` | After S2 + S3 stable |
| Combined audio stage (transcribe + voice + diarize) | `FRAMESET_CONCEPT.md` | Only if ffmpeg cost proves significant |
| ~~Prompt Library~~ | ~~`PROMPT_LIBRARY_CONCEPT.md`~~ | **Done — KB.S5** |
| Stage Loop Runner | `REFACTOR_CONCEPTS.md` | No KB quality impact; low priority |
| Area hash (spatial crop detection) | — | Removed from `hash` stage (64 pHash calls per image, high cost, edge-case use). If spatial near-duplicate detection becomes a real need, consider `imagehash.crop_resistant_hash()` or a coarser 3×3 grid as a separate opt-in stage. DB column `file_hashes.area_hash` still exists from migration 0007. |
| ClusterAssignment typing | `REFACTOR_CONCEPTS.md` | Defer until fourth cluster type added |
| Vocabulary review improvements | `UI_REDESIGN_CONCEPT.md` | After pipeline workbench |
| Export page framing | `UI_REDESIGN_CONCEPT.md` | After pipeline workbench |
| Smart Culling App | `CULLING_APP_CONCEPT.md` | Separate downstream tool |

---

## Concept Documents

| Document | What it covers |
|---|---|
| `FRAMESET_CONCEPT.md` | FrameSet + AudioTrack full design; Option A and B; open questions answered in session 2026-06-23 |
| `REFACTOR_CONCEPTS.md` | FileContext, LLMSession, Stage Loop Runner, ClusterAssignment |
| `PROMPT_LIBRARY_CONCEPT.md` | Per-KB named prompt variants in knowledge.db; requires KB.S4 |
| `UI_REDESIGN_CONCEPT.md` | Pipeline workbench, review redesign, health page, nav, file browser |
| `CULLING_APP_CONCEPT.md` | Separate culling application consuming KB Builder exports |
| `KNOWLEDGE_SETTINGS_CONCEPT.md` | People/Places/Dates domain toggles; entwinement survey; `KB.AM1`–`KB.AM3` |
