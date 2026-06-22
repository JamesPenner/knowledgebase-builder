# Sprint KB.Q4 — Knowledge Section: Unified People Registry + Nav Cleanup

## Goal

Add `/knowledge/people` — a unified person registry showing each person's voice cluster count and face cluster count. Provide add/edit/delete/merge operations on the people table. Relocate the Speakers review page from `/review/speakers` to `/knowledge/people/speakers` (with a redirect from the old URL), and finalize the Knowledge section nav structure. This completes the Knowledge section introduced in Q1–Q3.

## Baseline

1025 tests (committed at KB.Q3).

## Scope

Read/write to knowledge.db `people` table and cross-DB reads from corpus.db `voice_speaker_clusters` and `face_clusters` for counts. No changes to pipeline stages, the DAG, or the face/voice detection logic. The Speakers page UI is unchanged — only its URL changes.

## Deliverables

### New files

- `templates/people_registry.html` — full page; person list on the left, add/edit/merge form panel on the right
- `templates/partials/person_list.html` — HTMX swap target; table of people with voice and face cluster counts and action buttons
- `templates/partials/person_detail.html` — HTMX swap target; edit form + merge form for a selected person
- `tests/unit/test_people_registry_unit.py` — DB helper unit tests
- `tests/integration/test_people_registry_integration.py` — page load, CRUD, merge, delete safety, nav redirect

### Modified files

- `src/api/ui.py` — add `/knowledge/people` page route; add `/knowledge/people/speakers` route (copy of current `/review/speakers` route); add 301 redirect from `/review/speakers` to `/knowledge/people/speakers`; add HTMX partial routes for person list and detail
- `src/api/knowledge.py` — add people CRUD and merge routes
- `src/db/kb.py` — add `get_people_with_cluster_counts`, `delete_person`, `merge_people`
- `templates/base.html` — finalize Knowledge section nav; replace Speakers link with People link; Speakers page remains accessible via `/knowledge/people/speakers`
- `templates/speaker_review.html` — update any internal links that reference `/review/speakers` to `/knowledge/people/speakers`

## API Routes (`src/api/knowledge.py`)

```
GET  /api/knowledge/people
     → {
         "people": [
           {
             "id": int,
             "preferred_name": str,
             "voice_cluster_count": int,
             "face_cluster_count": int
           },
           ...
         ]
       }
     Counts are computed by opening corpus_conn alongside kb_conn and
     running COUNT queries on voice_speaker_clusters and face_clusters
     grouped by person_id.

POST /api/knowledge/people
     body: {"preferred_name": str}
     → {"id": int, "preferred_name": str}
     Returns 422 if preferred_name is blank or already exists.

PUT  /api/knowledge/people/{person_id}
     body: {"preferred_name": str}
     → updated person dict
     Returns 404 if person_id not found; 422 if name blank.

DELETE /api/knowledge/people/{person_id}
     → {}
     Returns 422 if the person has any assigned voice_speaker_clusters
     or face_clusters (person_id IS NOT NULL in either table). The error
     body includes a human-readable message listing which clusters block
     the delete.
     Returns 404 if person_id not found.

POST /api/knowledge/people/{person_id}/merge
     body: {"merge_from_id": int}
     → {"merged_into": int}
     Steps (all in a single transaction spanning both dbs via ATTACH):
       1. UPDATE face_clusters SET person_id=person_id WHERE person_id=merge_from_id
       2. UPDATE voice_speaker_clusters SET person_id=person_id, label=...
          WHERE person_id=merge_from_id
       3. Weighted-average voice_centroid using existing merge_voice_centroid()
          (from kb.py) for each reassigned voice cluster
       4. DELETE FROM people WHERE id=merge_from_id
     Returns 404 if either id not found; 422 if person_id == merge_from_id.
```

## DB Helpers (`src/db/kb.py`)

```python
def get_people_with_cluster_counts(
    kb_conn: sqlite3.Connection,
    corpus_conn: sqlite3.Connection,
) -> list[dict]:
    """Return all people with voice_cluster_count and face_cluster_count.
    Queries people from kb_conn; counts from corpus_conn."""

def delete_person(
    kb_conn: sqlite3.Connection,
    corpus_conn: sqlite3.Connection,
    person_id: int,
) -> None:
    """Delete person by id. Raises ValueError if any clusters are assigned.
    Raises KeyError if person_id not found."""

def merge_people(
    kb_conn: sqlite3.Connection,
    corpus_conn: sqlite3.Connection,
    keep_id: int,
    merge_from_id: int,
) -> None:
    """Reassign all face and voice clusters from merge_from to keep.
    Weighted-average the voice centroid. Delete merge_from person.
    Raises ValueError if keep_id == merge_from_id.
    Raises KeyError if either id not found."""
```

## URL Migration

| Old URL | New URL | Status |
|---|---|---|
| `/review/speakers` | `/knowledge/people/speakers` | 301 redirect |
| `/review/speakers/partials/queue` | `/knowledge/people/speakers/partials/queue` | new route |
| `/review/speakers/partials/decisions` | `/knowledge/people/speakers/partials/decisions` | new route |
| `/review/speakers/decide` | `/knowledge/people/speakers/decide` | new route (POST) |
| `/review/speakers/decisions/{id}` | `/knowledge/people/speakers/decisions/{id}` | new route (DELETE) |

Old partial and action routes do not redirect (HTMX posts/deletes should not follow redirects). The speaker_review.html template is updated to use the new paths directly. The `/review/speakers` page redirect covers browser navigation and bookmarks only.

## Final Nav Structure (`templates/base.html`)

```html
<a class="nav-link" href="/pipeline?kb={{ kb }}">Pipeline</a>
<a class="nav-link" href="/review/normalise?kb={{ kb }}">Normalise</a>
<a class="nav-link" href="/review/suggest?kb={{ kb }}">
  Suggest <span id="suggest-badge" ...></span>
</a>
<a class="nav-link" href="/review/new-terms?kb={{ kb }}">New Terms</a>
<span class="nav-sep">·</span>
<span class="nav-label">Knowledge</span>
<a class="nav-link" href="/knowledge/locations?kb={{ kb }}">Locations</a>
<a class="nav-link" href="/knowledge/locations/registry?kb={{ kb }}">Registry</a>
<a class="nav-link" href="/knowledge/people/faces?kb={{ kb }}">Faces</a>
<a class="nav-link" href="/knowledge/people/speakers?kb={{ kb }}">Speakers</a>
<a class="nav-link" href="/knowledge/people?kb={{ kb }}">People</a>
<span class="nav-sep">·</span>
<a class="nav-link" href="/corpus-stats?kb={{ kb }}">Stats</a>
<a class="nav-link" href="/health?kb={{ kb }}">Health</a>
```

## Test Targets

- Unit: `get_people_with_cluster_counts` (3 — with data, empty, counts correct), `delete_person` (3 — success, blocked by voice cluster, blocked by face cluster), `merge_people` (4 — success, same id rejected, missing keep, missing merge_from) — 10 tests
- API: GET people (2 — with data, empty), POST add (2 — success, blank name), PUT edit (2 — success, not found), DELETE (3 — success, blocked, not found), merge (3 — success, same id, not found) — 12 tests
- Integration: page loads (1), add person persists (1), edit name persists (1), delete blocked when clusters assigned (1), delete succeeds when no clusters (1), merge reassigns clusters and deletes person (1), merge weighted-averages voice centroid (1), `/review/speakers` redirects to new URL (1), speaker HTMX partials work at new paths (2) — 10 tests
- Nav: base.html renders full Knowledge section with People link and no Speakers link at old position (1) — 1 test (update existing nav test)

**Target: 1025 → 1060+ (+35)**

## Acceptance Criteria

1. `GET /knowledge/people?kb=<name>` renders a person registry with voice and face cluster counts per person
2. Adding a person creates a new row in knowledge.db `people` table
3. Editing a person's name persists the change
4. Deleting a person with assigned clusters returns a 422 with an explanatory message
5. Deleting a person with no assigned clusters succeeds
6. Merging two people reassigns all face and voice clusters from the dropped person to the kept person, weighted-averages the voice centroid, and deletes the dropped person record
7. `GET /review/speakers` issues a 301 redirect to `/knowledge/people/speakers`
8. The Speakers page renders correctly at its new URL; all HTMX actions (assign, unassign) work at the new paths
9. The nav shows the full Knowledge section (Locations, Registry, Faces, Speakers, People) with no Speakers link at its old position
10. All 1060+ tests pass; ruff clean
