# Concept: Knowledge Settings — Domain Toggles

## Core Framing

KB Builder lets a user selectively decide what kind of knowledge a corpus
should build — People, Places, Dates & Events — without any structural
schema change. Toggling a domain off does not remove tables or columns; it
gates which functions are allowed to *run* and which already-derived content
is allowed to *surface* downstream. A user with a video-heavy collection who
doesn't want people identified should be able to flip "People" off and run
every stage in ML Analysis/Enrichment without `face`, `face_meta`, `voice`,
`voice_diarize`, or `attribute_speakers` doing anything.

This is a UI/gating feature, not a schema feature — see `VISION.md`'s
two-database design brief: `knowledge.db` structure is unaffected.

---

## 1. Entwinement Survey

Before designing the toggle, the codebase was searched for every place two or
more of {people, places, dates} get combined, since a naive "skip the stage"
model breaks as soon as two domains are entwined in one function (e.g. a
person's birthday needs both People and Dates).

### Found

| # | Location | Domains | Nature |
|---|---|---|---|
| 1 | `classify.py::_life_event_tags` | People × Dates | Generates birthday/anniversary/memorial tags from a person match + file date |
| 2 | `attribute_speakers.py` | People × Voice | Writes a person's `preferred_name` onto transcript segments via voice-cluster time-overlap matching |
| 3 | `src/text/context.py::build_file_context` | People × Places × Dates × Voice | Shared aggregator consumed by **four** stages — `describe.py`, `suggest.py`, `summarize.py`, `retag.py`. Bundles `entity_names` (people + place entity matches deduped with no table filter), `metadata_location`, `metadata_date`, transcript (speaker-attributed when available), and `derived_tags` (including life-event tags). This is the highest-impact finding: every LLM-driven prompt in the pipeline already mixes all three domains with zero filtering. |
| 4 | `export.py::_write_search_text` | People × Places × Dates | A **second, independent** implementation of the same blending idea as #3 — its own SQL query joining tags + `file_entity_matches.matched_value` + description into `search_text.csv`. Not derived from `FileContext`, so it will drift out of sync with #3 if fixed separately. |

### Ruled out (checked, no entwinement found)

- `classify_rules.py` `BUILTIN_RULES` — calendar/technical/temporal/quality
  rules are all single-field. No rule combines person + location + date.
- `gps_cluster.py`, `geo_meta.py`, `geolocate.py` — Places-only.
- `temporal.py` — Dates-only.
- `vocab_llm.py` (synonym/thematic/taxonomy suggestions) — doesn't touch
  entity or location tables directly; only contaminated transitively through
  whatever `FileContext`/tags already contain, so fixing #3 and #4 is
  sufficient — no independent fix needed here.
- `build_taxonomy_data` — People, Places, and calendar tags are already
  separate top-level keys in the generated taxonomy. Clean.

### Existing precedent

`export.py::_write_people` already takes an `export_biometric: bool` gate
parameter — proof the codebase already has this shape of on/off knob at
export time. The new gating engine extends this pattern rather than
inventing a new one.

---

## 2. Data Model

One new table in `knowledge.db`:

```sql
CREATE TABLE knowledge_settings (
    category TEXT PRIMARY KEY,   -- 'people' | 'places' | 'dates'
    enabled  INTEGER NOT NULL DEFAULT 1
);
```

Seeded with all three rows `enabled = 1` at KB creation. Existing KBs get the
same seed via the migration itself — **opt-out, not opt-in**, so no behaviour
change for any KB until a user actively flips a toggle.

**No new schema for sub-toggles.** "Christmas yes, Halloween no" is already
solvable: `classify_rules` has a `category` column (`calendar` for
holiday/season rules) and a per-row `enabled` flag, both used today by the
existing Classify Rules manager. The Dates & Events row in Settings is a
filtered view into that manager plus one additional master switch — not a
new mechanism.

People and Places stay single on/off switches. No per-person or per-place
sub-toggle is in scope — nothing in the request calls for it, and the
existing People/Location registries already let a user delete or not-seed
individual entities if they want finer control.

**Custom entity tables are out of scope.** `entity_table_registry` is
generic (a KB can register arbitrary tables like "pets" via bundle import,
KB.P7) and has no domain classification column. Only the two built-in tables
(`people`, `locations`) are wired into the People/Places toggles. Extending
this to arbitrary custom tables is a real future need but not implied by
this request — flagged here, not solved here.

---

## 3. Gating Engine

New `src/pipeline/knowledge_gates.py`:

```python
STAGE_REQUIRES: dict[str, frozenset[str]] = {
    "face": frozenset({"people"}),
    "face_meta": frozenset({"people"}),
    "voice": frozenset({"people"}),
    "voice_diarize": frozenset({"people"}),
    "attribute_speakers": frozenset({"people"}),
    "geolocate": frozenset({"places"}),
    "geo_meta": frozenset({"places"}),
    "temporal": frozenset({"dates"}),
}

TAG_CATEGORY_REQUIRES: dict[str, frozenset[str]] = {
    "calendar": frozenset({"dates"}),
    "temporal": frozenset({"dates"}),
    "life_event": frozenset({"people", "dates"}),
    # "technical", "quality": no entry → always allowed
}
```

Stages listed in `STAGE_REQUIRES` are skipped outright — not just
correctness, but avoids loading GPU models (face/voice) for a disabled
domain. `classify` and `entity_match` are mixed-domain and can't be gated at
the stage level:

- `entity_match` already loops over `get_entity_tables(kb_conn)` — filtering
  out the `people` row when People is off and `locations` when Places is off
  is a one-line change to that loop, no restructuring needed.
- `classify` filters its `rules` list by `TAG_CATEGORY_REQUIRES` before the
  main loop, and adds a domain check to the existing
  `if all_life_events:` guard (must be `people` **and** `dates` enabled).

---

## 4. Closing the Chokepoints (findings #3 and #4)

`build_file_context()` gains an `enabled_categories: frozenset[str]`
parameter and filters:

- `entity_names` — needs `table_name` preserved through the dedup (currently
  discarded); only include `people`-table matches if `people` enabled,
  `locations`-table matches if `places` enabled.
- `metadata_location` — blanked if `places` disabled.
- `metadata_date` — **stays populated regardless of the `dates` setting.**
  Decided during design: a bare capture timestamp reads as structural
  metadata, not "knowledge about the photo's content," so it isn't
  suppressed the way calendar tags and life events are. Only content gated
  by `TAG_CATEGORY_REQUIRES` is filtered.
- `transcript` — speaker labels fall back to the existing generic label path
  (mirrors `attribute_speakers.py::_resolve_label`'s fallback) when `people`
  is disabled, rather than a person's resolved name.
- `derived_tags` — filtered by `TAG_CATEGORY_REQUIRES` **at read time**, not
  only relying on `classify` having respected the setting when the tag was
  written. This matters: if `dates` was on when `classify` ran and a user
  disables it afterward without re-running `classify`, stale calendar tags
  are still sitting in `file_derived_tags` — the filter has to live wherever
  the data is consumed.

`export.py::_write_search_text` is rewritten to use the same filter helper
`build_file_context` uses, instead of maintaining its own bespoke SQL blend —
this is what prevents finding #3 and #4 from drifting apart again.

---

## 5. UI

A collapsible "Knowledge Settings" panel at the top of the Pipeline Workbench
(`/pipeline`), using the same collapsible-header pattern as the Sources
panel (KB.U1). Three toggle rows — People, Places, Dates & Events — each
with a one-line consequence description. Dates & Events expands via
`<details>`/`<summary>` (matching KB.AF1's taxonomy tree pattern) into the
calendar-category classify rules, reusing the existing Classify Rules
manager's per-rule enable checkboxes rather than a new form.

When a domain is off, affected stage rows in the pipeline table show a
"Skipped — People disabled" badge instead of a Run button, consistent with
KB.T1's gate-banner language. Mixed-domain stages (`classify`) stay runnable
but show a smaller "Partial — Dates disabled" note, since disabling Dates
doesn't make the whole stage inert (technical/quality rules still apply).

---

## 6. Sprint Breakdown

| Sprint | Scope | Risk |
|---|---|---|
| `KB.AM1` | Schema + gating engine + stage skip logic + in-stage filtering (`entity_match`, `classify`) + settings API/CLI. No UI. | Low — additive, testable via API/CLI |
| `KB.AM2` | `build_file_context` category filtering + `_write_search_text` consolidation | High — touches 4 LLM stage call sites; isolated on purpose |
| `KB.AM3` | Knowledge Settings UI panel + cascading gate badges | Low — UI over an already-working backend |

See `sprints/planned/KB.AM1.md`, `KB.AM2.md`, `KB.AM3.md` for acceptance
criteria.
