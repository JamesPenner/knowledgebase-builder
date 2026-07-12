# KB.AM3 — Knowledge Settings: UI

**Status:** Planned
**Preceding sprint:** KB.AM2 (Knowledge Settings: Context & Export Filtering)
**Concept doc:** `sprints/planned/KNOWLEDGE_SETTINGS_CONCEPT.md`

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
- The existing Classify Rules manager's per-rule enable checkboxes (reused
  as-is for calendar sub-toggles — no new rule-editing UI).

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
  classify rules with their existing individual enable checkboxes.

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

## Test Coverage Expectations

- Integration tests for the settings panel: page load, toggle round-trip via
  the partial, Dates & Events expansion rendering the correct calendar
  rules.
- Integration tests for cascading badge state across all three toggles,
  including the `classify` partial-note case.
- Manual verification in a browser per the working agreement's UI-change
  requirement: toggle each domain off/on and confirm the affected stage rows
  visibly update, before declaring the sprint complete.
