# Sprint KB.Q2 — Knowledge Section: Location Registry

## Goal

Add `/knowledge/locations/registry` as a second Locations page under the Knowledge nav section. Browses and edits the location entity tables stored in knowledge.db (dynamically-named tables such as `entity_locations` and `entity_gps_cluster_locations`). Surfaces near-duplicate location name warnings using the P25 difflib similarity logic, and provides inline edit and merge actions.

## Baseline

955 tests (committed at KB.Q1).

## Scope

Read/write access to knowledge.db entity tables only. No changes to corpus.db, pipeline stages, or the DAG. The entity table enumeration query must be verified against SPEC.md §Entity Tables during the pre-sprint ritual — entity table schema and registration metadata are fully specified there.

## Deliverables

### New files

- `templates/location_registry.html` — full page; left panel is the entry list with near-duplicate badges, right panel is the edit/merge form
- `templates/partials/registry_entry_list.html` — HTMX swap target; renders all tables and their entries
- `templates/partials/registry_edit_form.html` — HTMX swap target; inline edit form for a single entry
- `tests/unit/test_knowledge_registry_unit.py` — DB helper unit tests; near-duplicate logic unit tests
- `tests/integration/test_knowledge_registry_integration.py` — page load, edit, delete, merge, near-duplicate detection

### Modified files

- `src/api/knowledge.py` — add registry routes
- `src/api/ui.py` — add `/knowledge/locations/registry` page route
- `src/db/kb.py` — add `get_entity_location_tables`, `get_entity_table_entries`, `update_entity_table_entry`, `delete_entity_table_entry`, `merge_entity_table_entries`
- `templates/base.html` — add Registry link under Knowledge section in nav

## API Routes (`src/api/knowledge.py`)

```
GET  /api/knowledge/locations/registry
     → {
         "tables": [
           {
             "name": str,          # e.g. "entity_gps_cluster_locations"
             "match_type": str,    # "gps" or "text"
             "entries": [
               {
                 "id": int,
                 "location": str,
                 "latitude": float | null,
                 "longitude": float | null,
                 "threshold_m": float | null
               },
               ...
             ],
             "near_duplicates": [
               {"a_id": int, "b_id": int, "score": float},
               ...
             ]
           },
           ...
         ]
       }
     near_duplicates computed server-side at load time using difflib
     SequenceMatcher over normalised location names (same algorithm as P25).
     Only pairs with score >= 0.85 are included.

PUT  /api/knowledge/locations/registry/{table}/{entry_id}
     body: {"location"?: str, "latitude"?: float, "longitude"?: float,
            "threshold_m"?: float}
     → updated entry dict
     Updates only the provided fields. Unknown table or id → 404.

DELETE /api/knowledge/locations/registry/{table}/{entry_id}
     → {}
     Unknown table or id → 404.

POST /api/knowledge/locations/registry/merge
     body: {"table": str, "keep_id": int, "drop_id": int}
     → {"merged_into": int, "table": str}
     Copies non-null fields from drop entry into keep entry where keep has
     null values (latitude, longitude, threshold_m). Then deletes drop entry.
     Returns 404 if table or either id does not exist.
     Returns 422 if keep_id == drop_id.
```

## DB Helpers (`src/db/kb.py`)

```python
def get_entity_location_tables(conn: sqlite3.Connection) -> list[dict]:
    """Return list of {name, match_type} for all registered entity tables
    with match_type in ('gps', 'text') that contain a 'location' column.
    Reads from the entity table registry (see SPEC.md §Entity Tables)."""

def get_entity_table_entries(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    """Return all rows from the named entity table. Raises ValueError for
    unknown or unregistered tables."""

def update_entity_table_entry(
    conn: sqlite3.Connection, table: str, entry_id: int, fields: dict
) -> sqlite3.Row:
    """Update the given fields on a single entry. Raises ValueError for
    unknown table or missing id."""

def delete_entity_table_entry(
    conn: sqlite3.Connection, table: str, entry_id: int
) -> None:
    """Delete a single entry by id. Raises ValueError for unknown table or
    missing id."""

def merge_entity_table_entries(
    conn: sqlite3.Connection, table: str, keep_id: int, drop_id: int
) -> None:
    """Back-fill null columns in keep row from drop row, then delete drop row."""
```

## Near-Duplicate Logic

Reuse the string normalisation and `difflib.SequenceMatcher` approach from KB.P25. The comparison runs over the `location` column values within each table independently (cross-table duplicates are not checked). Normalisation: lowercase, strip punctuation, collapse whitespace.

The near-duplicate computation is done in Python (not SQL) after fetching all entries, since it is an O(n²) comparison on small sets (entity tables are typically < 1000 rows). Results are embedded in the GET /api/knowledge/locations/registry response as `near_duplicates` arrays — no separate endpoint.

In the UI, entries with a near-duplicate partner show an amber warning badge. Clicking the badge pre-fills the merge form with both entry ids so the user can review and confirm.

## Nav Change (`templates/base.html`)

```html
<span class="nav-sep">·</span>
<span class="nav-label">Knowledge</span>
<a class="nav-link" href="/knowledge/locations?kb={{ kb }}">Locations</a>
<a class="nav-link" href="/knowledge/locations/registry?kb={{ kb }}">Registry</a>
```

## Test Targets

- Unit: `get_entity_location_tables` (2), `get_entity_table_entries` (2), `update_entity_table_entry` (3 — valid, unknown table, unknown id), `delete_entity_table_entry` (2), `merge_entity_table_entries` (3 — backfill nulls, same id rejected, unknown table) — 12 tests
- Near-duplicate unit: score above threshold detected (1), score below threshold not returned (1), normalisation (2), empty table (1) — 5 tests
- API: GET registry with data (1), GET empty (1), PUT success (1), PUT unknown table (1), DELETE success (1), merge success (1), merge same-id (1) — 7 tests
- Integration: page loads (1), edit round-trip persists to knowledge.db (1), delete removes entry (1), merge back-fills and deletes (1), near-duplicate pair detected on load (1), re-seed after merge does not recreate deleted entry (1) — 6 tests

**Target: 955 → 985+ (+30)**

## Acceptance Criteria

1. `GET /knowledge/locations/registry?kb=<name>` renders all registered location entity tables and their entries
2. Editing an entry's label, coordinates, or threshold persists to knowledge.db and refreshes the list via HTMX
3. Deleting an entry removes it from the entity table
4. Merging two entries back-fills null fields from the dropped entry, then deletes the dropped entry
5. Near-duplicate pairs (score ≥ 0.85) are highlighted with amber badges; clicking pre-fills the merge form
6. Unknown table names or entry ids return 404; same-id merge returns 422
7. Registry link appears in the Knowledge nav section on all KB-scoped pages
8. All 985+ tests pass; ruff clean
