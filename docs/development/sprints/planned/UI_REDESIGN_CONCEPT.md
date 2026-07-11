# Concept: UI/UX Redesign — Workbench Philosophy

## Core Framing

KB Builder is a knowledge-building workbench, not a catalogue operations
dashboard. The UI should answer "what am I building and what do I want to do
next," not "what percentage of files have been processed." Gaps in corpus
coverage are expected and fine. The user is constructing a reusable
knowledgebase from a representative working set, not maintaining a complete
and accurate file catalogue.

This framing drives every redesign decision documented here.

---

## 1. Pipeline → Workbench

### Problems with the current pipeline page

- Flat table of 18+ stages with no grouping or dependency indication
- "Pending" badges on unprocessed stages look like failures
- No way to run multiple stages in sequence
- No way to scope which files a stage acts on
- No way to schedule a run
- No inline context — the user must already know what each stage does
- Review touchpoints (Normalise, Suggest) appear as run buttons identical to
  processing stages, obscuring that they require human decisions

### Stage groups

Stages are grouped by function and dependency order. Within a group, each
stage depends on the ones above it in the same group or all of the prior
group.

| Group | Stages | Character |
|---|---|---|
| **Discovery** | Ingest, Analyse | Sets up what the corpus knows about |
| **Metadata** | Extract Meta, Extract Fields, Hash, Validate, Temporal | Fast, no ML |
| **ML Analysis** | Describe, Transcribe, Summarize, Quality, Aesthetic, Face, Voice, Diarize | Slow, GPU-bound |
| **Enrichment** | Entity Match, Classify, Geolocate, Attribute Speakers | Synthesis |
| **Vocabulary** | Suggest, Retag | Knowledge-building against review queues |
| **Output** | Writeback, Export | Finalise and deliver |

Review touchpoints (Normalise between Discovery and Metadata; Suggest between
Vocabulary stages) are shown as **gates** — banners between groups explaining
that a human decision step is available and linking to the review page. They
are not run buttons.

### File scope selector

A scope selector at the top of the page controls which files each stage acts on.

| Mode | Behaviour |
|---|---|
| **Resume** (default) | Process only files not yet through this stage (current behaviour) |
| **Re-run** | Reset selected stages and reprocess all files |
| **New files** | Run ingest incrementally first, then continue only on files added since last ingest |
| **By source** | Limit to files from a selected source folder |
| **By file type** | Limit to images, video, or audio only |

"By source" and "by file type" are implemented as filter parameters on the
existing `get_pending_X_files()` queries — no new schema. These two modes
cover the primary exploratory seeding use case (e.g. "run describe on five
diverse videos to start building vocabulary").

Manual file selection (tick specific files from a list) is deferred until the
corpus file browser is built (see section 5).

The selected scope is shown persistently so the user always knows what they
are acting on.

### Multi-stage selection and run

Each stage row has a checkbox. A "Run selected" button at the top runs the
checked stages in dependency order. Unchecked dependencies of a checked stage
are automatically included and highlighted so the user sees what was added.

Shortcuts:
- **Run group** button on each group header — runs all stages in that group
- **Run all** button at the top — runs the full pipeline

### Inline help

Each stage row has a disclosure (`▸`) that expands to show:
- One-sentence description of what the stage does
- What it requires (prior stages or models)
- What it produces (which KB artifacts or corpus fields it populates)

### Dependency state

A stage where all dependencies are satisfied and files are pending is
highlighted as **ready**. A stage where a dependency is pending or failed is
greyed out with a note indicating what is blocking it.

### Browser-side scheduling

A datetime picker in the page header allows the user to schedule the current
scope + stage selection to start at a future time. Implemented via `setTimeout`
in the browser. A clear warning states that the browser tab must remain open.
Server-side scheduling (survives browser close) is deferred.

---

## 2. Face Review — Centroid Quality Focus

**Status:** Done — `KB.AJ2` (see `docs/development/sprints/complete/KB.AJ2.md`).
Implemented for both face and speaker/voice review (§3 below), sharing the
same backend classification logic. Metric used is mean cosine similarity of
assigned embeddings to the centroid, computed live rather than from the
`face_centroid_spread` column. The `review_base.html` page-shell migration
(tabs/action-legend/shared JS) was explicitly kept out of scope — the flat
layout gained the new quality section and stopping-point banner without it.

### Problem with the current face review

The current queue presents all unassigned face clusters and implies the task
is complete only when all are assigned. This conflates "all clusters processed"
with "person identity well-established," which is not the same thing. The
application's goal is a reliable centroid for detecting a person at scale —
not 100% cluster assignment coverage.

### Proposed changes

**Per-person centroid quality indicator.** For each person in the people
registry, show:
- Number of assigned face clusters
- Mean cosine similarity of assigned embeddings to the centroid (confidence)
- A "reliable" / "needs more samples" / "too few samples" status based on
  thresholds (e.g. reliable = ≥ 5 clusters, mean similarity ≥ 0.7)

**Cluster ranking.** Unassigned clusters are ranked by how similar they are
to existing person centroids, not by file order. The most likely matches appear
first. The user spends time on high-value decisions, not on confirming obvious
matches buried at the end of a long queue.

**Stopping point indicator.** When all registered people have a "reliable"
centroid, the review page shows a clear "Centroids reliable — further review
optional" state. The user knows they can stop.

**Unassigned clusters remain.** Unassigned clusters are not presented as a
problem. A cluster that does not correspond to any registered person is
expected and fine.

---

## 3. Voice / Speaker Review — Same Framing

**Status:** Done — `KB.AJ2`, alongside §2. See that section's status note.

Same philosophy as face review.

**Per-person voice quality indicator:**
- Number of assigned voice segments
- Mean cosine similarity to the speaker centroid
- "Reliable" / "needs more samples" / "too few samples" status

**Segment ranking:** Unassigned segments ranked by similarity to existing
centroids — most likely matches first.

**Stopping point indicator:** When all registered people have a reliable voice
centroid, the page shows a clear "Centroids reliable" state.

---

## 4. Health Page — System vs Corpus

### Problem

The current health page mixes two fundamentally different categories of
information:
- **System health** (tools missing, models not found, schema out of date)
  — these are genuine problems that block the pipeline
- **Corpus coverage** (N files not yet described, N clusters unassigned)
  — these are informational; in a knowledge-building tool they are expected

A user sees "Face clusters: 342 unassigned" and may think something is wrong.
It is not — they just haven't done face review yet, and they may never need to
for most of those clusters.

### Proposed structure

**Section A — System Health**
All checks that indicate a genuine blocker: tools not found, models not
configured, schema migration needed, scaffold files missing. Shown with ✓/✗
indicators. A clean system shows all green.

**Section B — Corpus Coverage**
All informational counts: % of files described, transcribed, hashed, etc.;
number of unassigned face/voice clusters; vocabulary size. Shown as a
dashboard of numbers, not pass/fail checks. No red/warning indicators.
Framed as "where you are in building the KB" rather than "what still needs
to be done."

---

## 5. Corpus File Browser

**Status:** Done — `KB.AK1` (see `docs/development/sprints/complete/KB.AK1.md`).
Implemented as a filter-based browser, not selection-based: row checkboxes /
arbitrary multi-select were dropped from scope, since the pipeline's scope
system has no file-ID-list dimension (removed in migration `0024` in favor of
pure criteria-based sets). "Use as scope" instead pushes the browser's active
`CorpusFilterSpec` filter panel into the workbench's scope bar via the same
`localStorage` key the workbench already reads. The backend corrections below
were confirmed during implementation: the table is `files` (not
`corpus_files`), and per-file state comes from `LEFT JOIN`s against each
stage's own table (`pipeline_checkpoints` is stage-aggregate only, not
per-file).

### Purpose

Enables the "select specific files for a focused pipeline run" workflow that
is central to the exploratory seeding use case. A user who wants to run
describe + transcribe on five diverse videos before committing GPU time to the
full corpus needs a way to identify and select those files.

### Scope (text-only first)

A paginated list of all files in the corpus showing:
- Filename
- File type (image / video / audio)
- Source folder
- Processing state per key stage (ingested / described / transcribed)
- Captured date (if extracted)
- File size

**Filtering:** by source, by file type, by processing state (e.g. "described
but not transcribed").

**Selection:** checkboxes per file; "Use as scope" button sets the pipeline
workbench scope to the selected files.

Thumbnails are deferred until the opportunistic frame cache (FrameSet Option B)
is evaluated and implemented. The text-only browser covers the core workflow.

### Backend

A new API endpoint `GET /api/kb/{name}/files` returning a paginated, filtered
list of files from `corpus_files` joined to `descriptions`, `transcriptions`,
and `pipeline_checkpoints`. No schema changes needed.

---

## 6. Navigation Restructure

### Current nav

Pipeline · Normalise · Suggest · New Terms · Stats · Health · | · Knowledge:
Locations · Registry · Faces · Speakers · People

This has grown organically and mixes pipeline controls, review queues, corpus
information, and knowledge registry pages without a clear hierarchy.

### Proposed structure

| Section | Items |
|---|---|
| **Build** | Workbench (pipeline) |
| **Review** | Normalise · Suggest · New Terms |
| **Knowledge** | Locations · Registry · People · Faces · Speakers |
| **Corpus** | Files · Stats · Health |

"Build" is the primary action area. "Review" groups all the human-decision
queues. "Knowledge" is the registry/identity section. "Corpus" is
informational.

The review badge (currently only on Suggest) extends to Normalise and New
Terms when there are pending items.

---

## 7. Vocabulary Review Improvements

The suggest/vocabulary review page needs changes consistent with the
knowledge-building framing:

- **Remove implied completeness.** The queue should not suggest the user must
  approve every item. Framing: "Here are candidate terms — approve the ones
  that belong in this domain's vocabulary."
- **Level indicators.** Show whether a candidate comes from Level A
  (statistical frequency) or Level B (co-occurrence cluster). These have
  different confidence characteristics and the user benefits from knowing which
  is which.
- **Coverage summary.** A sidebar showing vocabulary size by domain area
  (if taxonomy is populated), so the user can see which areas are well-covered
  and which are sparse — and focus their review accordingly.

---

## 8. Export Page Framing

The export page currently shows a single Run button. Reframe it around KB
artifacts:

- What vocabulary terms have been built (count, top terms)
- What entity tables are populated
- What people/faces/speakers are registered
- What taxonomy structure exists

These are the things the downstream catalogue will consume. The export
summarises "what you've built" before the user commits to an export run.

---

## Sprint Planning Notes

These redesign areas can be prioritised independently. Suggested order based
on user impact and the core philosophy:

1. **Pipeline Workbench** (stage grouping + scope selector) — highest daily
   impact; makes the tool feel like a workbench rather than a dashboard
2. **Health page split** — quick win; removes false-alarm feeling from coverage
   gaps
3. **Navigation restructure** — low complexity, high coherence improvement
4. **Face/Voice review redesign** — requires centroid quality metrics from DB;
   medium complexity
5. **Corpus file browser** — enables file-level scope selection; medium backend
   work
6. **Vocabulary review improvements** — refinement; lower urgency
7. **Export framing** — low urgency; useful but not blocking

Backend refactor sprints (KB.S1–S4) should complete before the UI sprints to
ensure the LLM and text assembly layers are correct before the UI is redesigned
around them. The file browser depends on the frame cache only for thumbnails —
the text-only version can proceed immediately after the pipeline workbench.
