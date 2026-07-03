# KB.AI1 — Navigation Restructure

**Status:** Complete  
**Branch:** `clean-master`  
**Baseline:** 1685 tests  
**Result:** 1704 tests (+19 net)

## What was built

Replaced the flat organic nav with a four-section structure: **Build · Review · Knowledge · Corpus**.

| Section | Links |
|---|---|
| Build | Workbench (→ `/pipeline`) |
| Review | Normalise · Suggest · New Terms (each with a badge span) |
| Knowledge | Locations · Registry · People · Faces · Speakers · Prompts · Pattern Rules · Vocabulary |
| Corpus | Stats · Health |

- `static/js/nav_badges.js` — new unified badge poller; on DOMContentLoaded reads `data-kb` from `#kb-switcher`, fetches all three `/api/review/*/pending` endpoints, and populates `#normalise-badge`, `#suggest-badge`, `#new-terms-badge` when count > 0.
- `static/js/suggest_badge.js` — deleted (was already orphaned, not loaded anywhere).
- `templates/base.html` — four-section nav, `data-kb="{{ kb }}"` on `<select>`, added `nav_badges.js` script tag.
- No backend changes — all three pending endpoints already existed.

## Tests

- `tests/integration/test_nav.py` — 19 new tests across 5 classes (sections, Build links, Review links + badges, Corpus links, JS loading, multi-page verification)
- `tests/integration/test_ui.py` — updated 2 tests that previously asserted Suggest link and suggest_badge.js were absent
