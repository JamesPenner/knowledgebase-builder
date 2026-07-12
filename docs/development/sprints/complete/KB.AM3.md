# KB.AM3 — Knowledge Settings: UI

**Status:** Complete
**Preceding sprint:** KB.AM2 (Knowledge Settings: Context & Export Filtering, 1853 tests)
**Concept doc:** `sprints/planned/KNOWLEDGE_SETTINGS_CONCEPT.md`
**Result:** 1865 tests passing, 2 skipped (+12 net)

## Implementation Notes

- **Panel + gating are two separate HTMX partials**, both hosted under
  `/api/kb/{name}/...` (kb.py) and `/pipeline/groups` (ui.py):
  `GET /{name}/settings/panel` renders the toggle rows + Dates & Events
  calendar list; `GET /pipeline/groups` renders the full stage-groups
  block (extracted from `pipeline.html` into `partials/pipeline_groups.html`,
  now `{% include %}`-ed by the full page too). Toggling a domain fires both
  via `htmx.ajax(...)` — panel content and every stage row are recomputed,
  not just the ones that changed, since the two live in different template
  files and there's no cheap way to diff which rows actually need it. Simpler
  than per-row OOB swaps and still no full-page reload.
- **`_stage_state()` checks `checkpoint` before gating.** A stage that
  already ran stays "done" even if its domain is later disabled — gating
  stops *future* runs, it doesn't hide already-generated data (matches the
  concept doc's framing). Confirmed against the real `test-run` KB during
  manual verification: all 8 gated stages had prior checkpoints and stayed
  "done" with People disabled; only a fresh KB with no checkpoints showed
  the "skipped" state.
- **`classify`'s "Partial — Dates disabled" note is unconditional on People.**
  Per the acceptance criteria it fires only off the `dates` toggle, even
  though `life_event` tags also require `people`. Deliberately not extended
  — out of scope per the sprint doc, and adding it would be scope creep
  beyond what was asked.
- **New DB helper `set_classify_rule_enabled`** raises `LookupError` for an
  unknown rule id (→ 404) vs. `ValueError` for a non-calendar rule (→ 400) —
  distinct exception types so the API layer doesn't need to re-query to
  tell the two failure modes apart.
- **`get_classify_rules` gained an optional `category` filter** (backward
  compatible — its one existing caller, `classify.py`, passes neither new
  kwarg).

## Pre-Sprint Review Findings (confirmed against current code before implementation)

1. **No Classify Rules manager exists.** The "Builds On" section below
   originally assumed this sprint could reuse "the existing Classify Rules
   manager's per-rule enable checkboxes." A full codebase search found no
   such thing: no API endpoint, no template, no JS anywhere lists or
   toggles individual `classify_rules` rows. `get_classify_rules(kb_conn)`
   (`db/kb.py:583`) is a read-only helper used internally by `classify.py`
   and `generate-taxonomy` — not exposed to any UI. What KB.AB1 actually
   built was the *Pattern Rules* manager, a different table entirely
   (capture/replace/reject/ignore rules, not classify rules). Confirmed
   with the user before implementation: this sprint builds a **minimal**
   toggle list instead — calendar-category `classify_rules` rows with
   per-row enable checkboxes and one new endpoint that flips the existing
   `enabled` column. No rule creation, editing, or deletion; that remains
   future scope for a real Classify Rules manager if one is ever needed.
   The "Builds On" and acceptance-criteria sections below have been updated
   to reflect this.

## Goal

Surface the People/Places/Dates toggles built in `KB.AM1`/`KB.AM2` as a
**Knowledge Settings** panel at the top of the Pipeline Workbench, with
gated stages visibly reflecting their skipped/partial state — closing the
loop so a user never has to guess why a stage did nothing.

Named "Knowledge Settings," not "Settings" — `SPEC.md` already reserves
"Settings" for a separate, not-yet-built config.yaml-editing panel
(date resolution, tool paths). See `KB.AM1`'s Design Authority Updates
section.

## Builds On

- `KB.AM1`'s settings API (`GET`/`POST /api/kb/{name}/settings`).
- `KB.U1`'s collapsible Sources panel header pattern.
- `KB.AF1`'s `<details>`/`<summary>` tree pattern (for the Dates & Events
  expansion into calendar rules).
- `KB.T1`'s gate-banner language/visual pattern (for the skipped-stage
  badges).
- `classify_rules.enabled` (migration `0002_classify_and_people.sql`) and
  `get_classify_rules(kb_conn, enabled_only=...)` (`db/kb.py:583`) — the
  existing column and read helper the new minimal toggle list is built on
  (see Pre-Sprint Review Findings #1). No existing UPDATE helper for this
  column — one is new this sprint.

## Acceptance Criteria

### Knowledge Settings panel
- Collapsible panel at the top of `/pipeline`, same collapsible-header
  interaction as the Sources panel.
- Three toggle rows: People, Places, Dates & Events. Each shows a one-line
  consequence description (e.g. "Disables face/voice detection, speaker
  attribution, and birthday/anniversary tagging. People's names are also
  excluded from generated descriptions and summaries.").
- Toggling a row calls `POST /api/kb/{name}/settings` and re-renders the
  panel plus the affected pipeline stage rows via an HTMX partial —
  following the existing `pipeline.js` refresh pattern, not a full page
  reload.
- Dates & Events row expands via `<details>` to list calendar-category
  `classify_rules` rows (label + result_tag), each with an enable checkbox
  reflecting/toggling its `enabled` column via a new
  `PATCH /api/kb/{name}/classify-rules/{id}` endpoint (body:
  `{"enabled": bool}`). Toggling re-renders just that row via HTMX/JS —
  no full-page reload, no rule creation/editing/deletion.

### Cascading stage state
- Stage rows whose `STAGE_REQUIRES` entry is unmet show a "Skipped —
  {Category} disabled" badge in place of the Run button.
- `classify`'s row (mixed-domain, not fully gated) stays runnable but shows
  a smaller "Partial — Dates disabled" note when `dates` is off, to avoid
  implying the whole stage is inert when its technical/quality rules still
  apply.
- Badge/note state updates immediately after a toggle change, without
  requiring a page reload.

## Out of Scope

- Per-person or per-place sub-toggles.
- Any change to the underlying gating logic — this sprint is UI over an
  already-correct backend from `KB.AM1`/`KB.AM2`.
- A full Classify Rules manager (create/edit/delete rules, non-calendar
  categories, match-config editing). The Dates & Events expansion is
  strictly a read + enable-toggle list scoped to `category='calendar'`
  rows only.

## Test Coverage Expectations

- Integration tests for the settings panel: page load, toggle round-trip via
  the partial, Dates & Events expansion rendering the correct calendar
  rules.
- Integration tests for cascading badge state across all three toggles,
  including the `classify` partial-note case.
- Integration test for `PATCH /api/kb/{name}/classify-rules/{id}`: toggles
  `enabled`, rejects a non-calendar rule id or unknown id appropriately,
  persists across a re-fetch.
- Manual verification in a browser per the working agreement's UI-change
  requirement: toggle each domain off/on and confirm the affected stage rows
  visibly update, before declaring the sprint complete.
