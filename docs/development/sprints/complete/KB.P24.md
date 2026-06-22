# Sprint KB.P24 — GPS Cluster Analysis

## Goal

Group corpus files with GPS coordinates into geographic clusters using DBSCAN, name each cluster from existing geolabel data, store assignments in corpus.db, and provide an optional command to promote stable cluster labels into the knowledge.db entity system for entity matching and vocabulary suggestion.

## Baseline

900 tests (committed at KB.P23).

## Scope

Standalone corpus-maintenance and KB-enrichment operation. No changes to existing pipeline stages or the pipeline DAG. Clustering reads GPS coordinates already extracted by extract_fields and geolabels already resolved by geolocate. It does not re-run or depend on those stages completing — files with GPS but no geolabel fall back to a coordinate-derived name.

## Deliverables

### New files

- `src/migrations/corpus/0016_gps_clusters.sql` — two tables:
  - `gps_clusters (id PK, label, centroid_lat, centroid_lon, file_count, eps_km, min_samples, created_at)`
  - `file_gps_cluster_assignments (file_id PK FK, cluster_id FK nullable, distance_m)` — `cluster_id` is NULL for noise files
- `src/stages/gps_cluster.py` — `run_gps_cluster(corpus_path, kb_folder, config, progress, cancel_event, export=False)` + helpers
- `tests/unit/test_gps_cluster_unit.py`
- `tests/integration/test_gps_cluster_integration.py`

### Modified files

- `src/config.py` — add `gps_cluster_eps_km: float = 1.0`, `gps_cluster_min_samples: int = 3` under thresholds; wire into both `_extract_overridable` and `_extract_per_kb`
- `src/db/corpus.py` — DB helpers: `get_gps_clusters`, `get_gps_cluster_assignments_for_export`, `clear_gps_clusters`
- `src/cli/geolocate.py` — add `cluster` and `seed-clusters` sub-commands
- `src/stages/export.py` — `_write_gps_clusters(export_dir, corpus_conn)` → `gps_clusters.csv`; called on full export
- `tests/integration/test_schema.py` — add `gps_clusters` and `file_gps_cluster_assignments` to `_CORPUS_TABLES`

## Algorithm

DBSCAN with haversine distance (scikit-learn, lazy-imported). Clean-slate re-run: existing assignments and clusters are cleared before each run.

```python
from sklearn.cluster import DBSCAN
import numpy as np

coords_rad = np.radians(coords)           # coords = [[lat, lon], ...]
eps_rad = config.gps_cluster_eps_km / 6371.0
labels = DBSCAN(eps=eps_rad, min_samples=config.gps_cluster_min_samples,
                algorithm='ball_tree', metric='haversine').fit_predict(coords_rad)
```

Files with `label == -1` (DBSCAN noise) are recorded in `file_gps_cluster_assignments` with `cluster_id = NULL`.

## Cluster Naming

For each cluster, query `file_geolabels` for the members and pick the plurality name in priority order: `custom_region` → `state` → `country`. If no geolabels exist for any member, fall back to `"Cluster @ {lat:.3f},{lon:.3f}"`.

## CLI

```
enrich geolocate cluster --kb <name> [--eps-km 1.0] [--min-samples 3] [--export]
enrich geolocate seed-clusters --kb <name>
```

`cluster` runs DBSCAN and writes corpus.db. `seed-clusters` reads the current clusters and upserts them into a `gps_cluster_locations` entity table in knowledge.db (match_type `gps`, `threshold_m = eps_km * 1000`). This is idempotent — re-seeding after re-clustering updates existing rows.

## Export CSV

`gps_clusters.csv` columns: `path, cluster_id, cluster_label, centroid_lat, centroid_lon, distance_m`

One row per file that has a GPS cluster assignment (including noise files with null cluster_id). Written on `--export` or full export run.

## seed-clusters Entity Table Schema

Written to `entity_gps_cluster_locations` in knowledge.db:

| column | value |
|---|---|
| `location` | cluster label (key column) |
| `latitude` | centroid_lat |
| `longitude` | centroid_lon |
| `threshold_m` | `eps_km * 1000` |
| `file_count` | number of files in cluster |

Registered with `match_type = 'gps'` so the entity match GPS sub-pass picks it up automatically.

## Test Targets

- Unit: `_name_cluster` with geolabels (4), fallback to coordinate name (1), DB helpers (4), `_compute_cluster_label` edge cases (2) — ~11 tests
- Integration: schema tables present (1), happy path multiple clusters (1), noise files recorded (1), re-run clears and rebuilds (1), files without GPS skipped (1), export CSV (2), seed-clusters creates entity table (1), seed-clusters is idempotent (1), all-noise corpus (1) — ~10 tests
- Config tests: new fields present with defaults (2), per-KB override (1) — ~3 tests

**Target: 900 → 930+ tests (+30)**

## Acceptance Criteria

1. `enrich geolocate cluster --kb <name>` groups GPS-tagged files into clusters and prints a summary (`N clusters, M assigned, K noise`)
2. Files within `eps_km` of each other are grouped into the same cluster
3. Cluster labels use the plurality geolabel (custom_region > state > country), falling back to coordinates
4. Noise files appear in `file_gps_cluster_assignments` with `cluster_id = NULL`
5. Re-running clears and rebuilds all assignments (clean slate)
6. `--export` writes `gps_clusters.csv` with one row per GPS file
7. `enrich geolocate seed-clusters` creates/updates `entity_gps_cluster_locations` in knowledge.db, registered as a `gps` match-type entity table
8. All 930+ tests pass; ruff clean
