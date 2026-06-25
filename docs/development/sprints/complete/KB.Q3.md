# Sprint KB.Q3 — Knowledge Section: Face Cluster Review

## Goal

Close the review UI gap deferred in KB.P15. Add `/knowledge/people/faces` — a face cluster assignment page analogous to the Speakers page (KB.P18). Users can assign face clusters to known people (or create new people), with representative face thumbnails served on demand via PIL crops from the original corpus images. Requires a corpus migration to add `person_id` and `label` columns to `face_clusters`, mirroring the schema of `voice_speaker_clusters`.

## Baseline

985 tests (committed at KB.Q2).

## Scope

Image files only. Video face detection was explicitly deferred in KB.P15 and remains out of scope here. No changes to the face detection stage (`stages/face.py`) or the pipeline DAG. This sprint wires the existing `face_clusters` and `file_face_regions` corpus data to a review UI via new API routes and templates.

## Deliverables

### New files

- `src/migrations/corpus/0017_face_cluster_person.sql` — adds `person_id` and `label` columns to `face_clusters`
- `templates/face_review.html` — full page; two panels: pending clusters (queue) and assigned clusters (decisions)
- `templates/partials/face_clusters_queue.html` — HTMX swap target; renders unassigned clusters with thumbnail and people dropdown
- `templates/partials/face_clusters_assigned.html` — HTMX swap target; renders assigned clusters with person label and unassign button
- `tests/unit/test_face_review_unit.py` — DB helper unit tests; thumbnail crop logic unit tests
- `tests/integration/test_face_review_integration.py` — page load, assign, unassign, thumbnail route, migration, schema

### Modified files

- `src/api/ui.py` — add `/knowledge/people/faces` page route and HTMX partial routes
- `src/api/knowledge.py` — add face cluster routes and face thumbnail route
- `src/db/corpus.py` — add `get_pending_face_clusters`, `get_assigned_face_clusters`, `assign_face_cluster`, `unassign_face_cluster`, `get_face_cluster_representative`, `get_face_region_for_thumbnail`
- `templates/base.html` — add Faces link under Knowledge section in nav
- `tests/integration/test_schema.py` — verify `face_clusters` has `person_id` and `label` columns after migration

## Migration (`src/migrations/corpus/0017_face_cluster_person.sql`)

```sql
ALTER TABLE face_clusters ADD COLUMN person_id INTEGER REFERENCES people(id);
ALTER TABLE face_clusters ADD COLUMN label TEXT;
```

`people` here refers to the knowledge.db `people` table. The FK is a soft reference only — corpus.db does not ATTACH knowledge.db at migration time; the column is used for data integrity by convention, not enforced by SQLite FK constraints across databases.

## API Routes (`src/api/knowledge.py`)

```
GET  /api/knowledge/people/faces/clusters
     → {
         "pending": [
           {
             "id": int,
             "member_count": int,
             "spread": float,
             "representative": {
               "face_region_id": int,
               "file_path": str,
               "thumbnail_url": str   # "/api/corpus/face-thumbnail/{id}?kb=..."
             }
           },
           ...
         ],
         "assigned": [
           {
             "id": int,
             "member_count": int,
             "label": str,
             "person_id": int,
             "representative": { ... }
           },
           ...
         ]
       }

GET  /api/corpus/face-thumbnail/{face_region_id}
     Query: ?kb=name
     → image/jpeg response; PIL crop of bbox from original file
     Lazy — no stored thumbnails. Error fallback: 1×1 grey JPEG.
     Route is in knowledge.py router but prefixed /api/corpus/ to
     signal it reads corpus data.

POST /review/faces/decide
     Form fields: cluster_id (int), action (str: "assign"|"unassign"),
                  person_id (str, optional), new_name (str, optional)
     → Response with HX-Trigger: {"pendingChanged": null, "decisionsChanged": null}
     assign: requires person_id (existing) or new_name (creates via upsert_person);
             updates face_clusters.person_id and face_clusters.label
     unassign: clears person_id and label for the cluster

GET  /knowledge/people/faces/partials/queue
     → renders partials/face_clusters_queue.html

GET  /knowledge/people/faces/partials/assigned
     → renders partials/face_clusters_assigned.html
```

## Thumbnail Route Logic

```python
# In knowledge.py, lazy imports
def face_thumbnail(face_region_id: int, kb: str, ...):
    from PIL import Image
    import io

    face_row = get_face_region_for_thumbnail(corpus_conn, face_region_id)
    # face_row contains: file_path, x1, y1, x2, y2 (verify column names
    # against src/migrations/corpus/0015_*.sql before implementing)

    PADDING = 10
    try:
        img = Image.open(face_row["file_path"]).convert("RGB")
        box = (
            max(0, face_row["x1"] - PADDING),
            max(0, face_row["y1"] - PADDING),
            min(img.width,  face_row["x2"] + PADDING),
            min(img.height, face_row["y2"] + PADDING),
        )
        crop = img.crop(box)
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=85)
        return Response(content=buf.getvalue(), media_type="image/jpeg")
    except Exception:
        # Return 1×1 grey fallback so <img> tags never break the layout
        grey = Image.new("RGB", (1, 1), (128, 128, 128))
        buf = io.BytesIO()
        grey.save(buf, format="JPEG")
        return Response(content=buf.getvalue(), media_type="image/jpeg")
```

The actual bbox column names (`x1/y1/x2/y2` vs `bbox_x/bbox_y/bbox_w/bbox_h` etc.) must be verified against `src/migrations/corpus/0015_*.sql` during the pre-sprint ritual before writing `get_face_region_for_thumbnail`.

## DB Helpers (`src/db/corpus.py`)

```python
def get_pending_face_clusters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return face_clusters where person_id IS NULL, with member_count and spread."""

def get_assigned_face_clusters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return face_clusters where person_id IS NOT NULL, with label."""

def assign_face_cluster(
    conn: sqlite3.Connection, cluster_id: int, person_id: int, label: str
) -> None:
    """Set person_id and label on a face_cluster row."""

def unassign_face_cluster(conn: sqlite3.Connection, cluster_id: int) -> None:
    """Clear person_id and label on a face_cluster row."""

def get_face_cluster_representative(
    conn: sqlite3.Connection, cluster_id: int
) -> sqlite3.Row | None:
    """Return (face_region_id, file_path, bbox columns) for the first
    member of the cluster. Used to populate thumbnail_url in the API response."""

def get_face_region_for_thumbnail(
    conn: sqlite3.Connection, face_region_id: int
) -> sqlite3.Row | None:
    """Return (file_path, bbox columns) for a single face_region_id."""
```

## Nav Change (`templates/base.html`)

```html
<span class="nav-sep">·</span>
<span class="nav-label">Knowledge</span>
<a class="nav-link" href="/knowledge/locations?kb={{ kb }}">Locations</a>
<a class="nav-link" href="/knowledge/locations/registry?kb={{ kb }}">Registry</a>
<a class="nav-link" href="/knowledge/people/faces?kb={{ kb }}">Faces</a>
```

## Test Targets

- Migration: schema test confirms `person_id` and `label` columns exist on `face_clusters` (1) — 1 test
- Unit: `get_pending_face_clusters` (2), `get_assigned_face_clusters` (2), `assign_face_cluster` (2), `unassign_face_cluster` (1), `get_face_cluster_representative` (2 — with members, empty cluster), `get_face_region_for_thumbnail` (2 — found, missing) — 11 tests
- Thumbnail unit: crop happy path (1), missing file returns grey JPEG (1), padding clamps to image bounds (1) — 3 tests
- API: GET clusters with pending and assigned (1), GET empty corpus (1), thumbnail returns JPEG bytes (1), thumbnail missing face_region returns grey JPEG (1), decide assign to existing person (1), decide assign with new_name creates person (1), decide unassign (1), decide missing cluster_id (1) — 8 tests
- Integration: page loads (1), assign round-trip persists person_id (1), unassign clears person_id (1), thumbnail crops correct region from synthetic image (1), new person created on assign with new_name (1), HTMX partial routes return HTML (2) — 7 tests
- Schema: `face_clusters` has `person_id` and `label` after migration (already counted above) — integrated into test_schema.py (1 test counted in integration) — 10 tests total integration

**Target: 985 → 1025+ (+40)**

## Acceptance Criteria

1. `GET /knowledge/people/faces?kb=<name>` renders pending and assigned face cluster panels
2. Each pending cluster card shows a face thumbnail image (PIL crop from corpus file)
3. Assigning a cluster to an existing person sets `face_clusters.person_id` and `face_clusters.label`
4. Assigning with a new name creates the person via `upsert_person` and then assigns
5. Unassigning a cluster clears `person_id` and `label` and moves it back to pending
6. `GET /api/corpus/face-thumbnail/{id}?kb=<name>` returns a JPEG for a valid face region
7. A missing or unreadable source file returns a 1×1 grey JPEG (no 500 error)
8. The Faces nav link appears under Knowledge on all KB-scoped pages
9. `src/migrations/corpus/0017_face_cluster_person.sql` applied cleanly; `test_schema.py` passes
10. All 1025+ tests pass; ruff clean
