# Sprint KB.Q1 — Knowledge Section: Location Manager (GPS Cluster Map)

## Goal

Add a Knowledge section to the nav and build the first page under it: `/knowledge/locations`. Shows all GPS clusters on a Leaflet.js map (loaded from CDN, no build step) alongside a cluster list panel. Allows renaming a cluster label and promoting a cluster to a location entity in knowledge.db — closing the review loop on the KB.P24 backend.

## Baseline

934 tests (committed at KB.P25).

## Scope

No changes to existing pipeline stages or the pipeline DAG. This sprint is purely additive: new API routes, new DB helpers, new templates, and a nav update. The GPS cluster data and the `seed-clusters` promotion logic already exist; this sprint surfaces them through the web UI.

## Deliverables

### New files

- `src/api/knowledge.py` — new FastAPI router for all `/api/knowledge/*` routes; registered in `src/api/__init__.py`
- `templates/locations.html` — full page; left panel is the Leaflet map div, right panel is the cluster list partial
- `templates/partials/cluster_list.html` — HTMX swap target; renders the cluster table with rename form and promote button per row
- `tests/unit/test_knowledge_locations_unit.py` — DB helper unit tests
- `tests/integration/test_knowledge_locations_integration.py` — page load, rename, promote, edge cases

### Modified files

- `src/api/__init__.py` — import and include `knowledge.router` with prefix `/api/knowledge`
- `src/api/ui.py` — add `/knowledge/locations` page route
- `src/db/corpus.py` — add `rename_gps_cluster`, `get_gps_cluster_with_assignments`
- `templates/base.html` — add Knowledge section label and Locations link to nav

## API Routes (`src/api/knowledge.py`)

All routes follow the existing `Depends(resolve_kb)` pattern and return JSON.

```
GET  /api/knowledge/locations/clusters
     → {"clusters": [{"id", "label", "centroid_lat", "centroid_lon",
                       "file_count", "eps_km", "created_at"}, ...]}

POST /api/knowledge/locations/clusters/{cluster_id}/rename
     body: {"label": str}
     → {"cluster_id": int, "label": str}

POST /api/knowledge/locations/clusters/{cluster_id}/promote
     → {"status": "promoted", "entity_table": "entity_gps_cluster_locations",
        "label": str}
     Runs the single-cluster upsert into entity_gps_cluster_locations in
     knowledge.db. Idempotent — re-promoting an already-promoted cluster
     updates the existing row.
```

## DB Helpers (`src/db/corpus.py`)

```python
def rename_gps_cluster(conn: sqlite3.Connection, cluster_id: int, label: str) -> None:
    """Update the label column of gps_clusters for the given id."""

def get_gps_cluster_with_assignments(
    conn: sqlite3.Connection, cluster_id: int
) -> dict:
    """Return cluster row + list of assigned file paths (for detail panel)."""
```

## Promote Logic

`POST .../promote` does exactly what `enrich geolocate seed-clusters` does, but for a single cluster:

```python
# In knowledge.py router, lazy-import from src.db.kb
from src.db.kb import open_kb, upsert_entity_table_row
```

The entity table `entity_gps_cluster_locations` must be registered if it doesn't already exist (call `ensure_entity_table` or equivalent). Columns: `location`, `latitude`, `longitude`, `threshold_m`, `file_count`. Same logic as `run_gps_cluster.seed_clusters` — this route is a single-row variant of that operation.

## Frontend

`templates/locations.html` loads Leaflet from CDN:

```html
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
```

`static/js/locations_map.js` — initialises the map, fetches `/api/knowledge/locations/clusters?kb=…`, places a `L.circle` for each cluster (radius proportional to `eps_km`). Clicking a circle highlights the corresponding row in the cluster list panel.

The cluster list partial renders an inline rename form (`<input>` + HTMX `hx-post`) and a Promote button per row. After promote, the row gains a `promoted` badge and the Promote button is disabled.

## Nav Change (`templates/base.html`)

Add after the existing Suggest/New Terms links:

```html
<span class="nav-sep">·</span>
<span class="nav-label">Knowledge</span>
<a class="nav-link" href="/knowledge/locations?kb={{ kb }}">Locations</a>
```

`nav-label` is a new CSS class: muted, smaller, non-clickable — visually groups the Knowledge links without requiring a dropdown.

## Test Targets

- Unit: `rename_gps_cluster` (2), `get_gps_cluster_with_assignments` (2), promote logic upsert (2) — 6 tests
- API: GET clusters (2 — with data, empty corpus), rename (2 — success, bad id), promote (3 — fresh, idempotent, no clusters) — 7 tests
- Integration: page loads (1), cluster list partial (1), rename round-trip (1), promote creates entity row (1), promote is idempotent (1), corpus with no GPS files returns empty (1) — 6 tests
- Nav: base.html renders Knowledge label and Locations link (1) — 1 test

**Target: 934 → 955+ (+21)**

## Acceptance Criteria

1. `GET /knowledge/locations?kb=<name>` renders a page with a Leaflet map and a cluster list
2. The map shows one marker/circle per GPS cluster at the correct centroid coordinates
3. Renaming a cluster via the inline form updates `gps_clusters.label` and refreshes the list via HTMX
4. Promoting a cluster upserts a row into `entity_gps_cluster_locations` in knowledge.db
5. Re-promoting (idempotent) does not create a duplicate row
6. A corpus with no GPS data shows an empty map and a "No clusters — run `enrich geolocate cluster` first" message
7. The Knowledge section label and Locations nav link appear for all KB-scoped pages
8. All 955+ tests pass; ruff clean
