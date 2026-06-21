# Sprint KB.P6 — Suggest Nav Link + Pending Badge

## Scope

Adds a "Suggest" link to the shared nav bar in `base.html`, with a small JS-populated badge showing the pending candidate count. Currently the Suggestion Review page is unreachable from the nav — users must type or guess the URL. This sprint makes the review touchpoint discoverable from any page, and surfaces pending work via a lightweight badge that fetches the count on page load without requiring any changes to server-side route handlers.

No new API endpoints, no DB changes, no new routes.

## Builds On

- KB.5: `candidates` table, `get_candidate_counts()`, `/api/review/suggest/pending` endpoint
- KB.P5: `kb_switcher.js` pattern for client-side nav augmentation; `base.html` nav structure

## Baseline

351 tests passing, ruff clean.

## Deliverables

### New files
- `static/js/suggest_badge.js` — on `DOMContentLoaded`, reads `data-kb` from the badge span, fetches `/api/review/suggest/pending?kb=<name>&limit=0`, reads `data.counts.pending`, sets badge text and makes it visible if count > 0

### Modified files
- `templates/base.html` — add Suggest nav link (after Normalise, before Stats) inside the `{% if kb %}` block; add badge `<span id="suggest-badge" class="nav-badge" data-kb="{{ kb }}" style="display:none"></span>` inside the link; add `<script src="/static/js/suggest_badge.js"></script>` alongside the existing `kb_switcher.js` script tag
- `static/css/main.css` — add `.nav-badge` rule (inline pill, hidden by default via `display:none` in HTML, shown by JS)
- `tests/integration/test_ui.py` — 4 new tests in a `# Suggest nav link (KB.P6)` section

## Acceptance Criteria

1. Any page rendered with a valid `?kb=` param shows a "Suggest" link in the nav between Normalise and Stats
2. The link points to `/review/suggest?kb=<name>`
3. A badge span is present in the nav link; when the JS runs and there are pending candidates, the badge shows the count
4. When pending count is 0, the badge remains hidden (no empty pill visible in the nav)
5. When no `?kb=` param is present, the Suggest link does not appear (consistent with existing nav guard)
6. GET `/review/suggest?kb=<name>` returns 200 with "Suggestion Review" in the response
7. `python -m pytest tests/ -q` → 355 tests passing; `ruff check src/ tests/` → 0 errors

## Test Targets — 4 new tests (all in `tests/integration/test_ui.py`)

- `test_nav_has_suggest_link` — GET /pipeline?kb=test → response contains `/review/suggest?kb=test`
- `test_nav_suggest_link_has_badge_span` — GET /pipeline?kb=test → response contains `id="suggest-badge"` and `data-kb="test"`
- `test_nav_includes_suggest_badge_js` — GET /pipeline?kb=test → response contains `suggest_badge.js`
- `test_suggest_review_page_returns_200` — GET /review/suggest?kb=test with empty DBs → 200, "Suggestion Review" in content

## Design Notes

### Why JS fetch, not HTMX partial

The existing `kb_switcher.js` already augments the nav client-side using a `DOMContentLoaded` fetch. `suggest_badge.js` follows the same pattern: one small file, a `data-kb` attribute, a single fetch, a DOM update. No new partial route, no new template file.

### Reusing the existing endpoint with `limit=0`

`GET /api/review/suggest/pending?kb=<name>&limit=0` already returns `{"items": [], "counts": {"pending": N, ...}}`. Passing `limit=0` suppresses the items list while `get_candidate_counts()` still runs. No new endpoint needed.

### Badge hidden when zero

The span starts with `style="display:none"` in the HTML. JS only calls `badge.style.display = 'inline'` when `pending > 0`. This avoids an empty pill flickering on page load.

### Fetch failure is silent

If the fetch fails (KB not yet initialised, network error), the badge stays hidden. It is non-critical UI and should never surface an error state.

### Nav order

New order: Pipeline · Normalise · **Suggest** · Stats. Suggest belongs between Normalise and Stats because it follows the Normalise touchpoint in the pipeline and precedes export/stats review.

## Out of Scope

- Vocabulary gap badge (discussed and ruled out — see KB.P6 planning conversation)
- Level C LLM Suggest (separate sprint)
- Badge color-coding by severity (single neutral color sufficient; the number itself carries the signal)
- Live badge refresh on candidate decisions (page reload is sufficient; HTMX push is not worth the complexity for a nav element)
