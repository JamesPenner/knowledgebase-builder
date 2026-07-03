"""GPS cluster analysis — group corpus files by geographic proximity using DBSCAN."""
import logging
import threading
from collections import Counter
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter
from src.stages.classify_rules import _haversine_m

logger = logging.getLogger(__name__)


def _load_geolabels(corpus_conn) -> dict[int, tuple[str | None, str | None, str | None]]:
    """Return {file_id: (custom_region, state, country)} for all geolocated files."""
    rows = corpus_conn.execute(
        "SELECT file_id, custom_region, state, country FROM file_geolabels"
    ).fetchall()
    return {r["file_id"]: (r["custom_region"], r["state"], r["country"]) for r in rows}


def _name_cluster(
    member_ids: list[int],
    geolabels: dict[int, tuple[str | None, str | None, str | None]],
    centroid_lat: float,
    centroid_lon: float,
) -> str:
    """Derive a cluster label from the plurality geolabel of its members."""
    names: list[str] = []
    for fid in member_ids:
        entry = geolabels.get(fid)
        if entry:
            name = entry[0] or entry[1] or entry[2]
            if name:
                names.append(name)
    if names:
        return Counter(names).most_common(1)[0][0]
    return f"Cluster @ {centroid_lat:.3f},{centroid_lon:.3f}"


def run_gps_cluster(
    corpus_path: Path,
    kb_folder: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    export: bool = False,
) -> dict:
    import numpy as np
    from sklearn.cluster import DBSCAN

    from src.db.corpus import (
        clear_gps_clusters,
        get_files_with_gps,
        open_corpus,
        parse_gps_value,
    )

    corpus_conn = open_corpus(corpus_path)
    try:
        files = get_files_with_gps(corpus_conn)

        if not files:
            progress.done()
            return {"clusters": 0, "assigned": 0, "noise": 0}

        coords_list = [(parse_gps_value(f["lat"]), parse_gps_value(f["lon"])) for f in files]
        # Filter out files where GPS could not be parsed
        valid = [(f, lat, lon) for f, (lat, lon) in zip(files, coords_list)
                 if lat is not None and lon is not None]
        if not valid:
            progress.done()
            return {"clusters": 0, "assigned": 0, "noise": 0}
        files = [v[0] for v in valid]
        coords = np.array([(v[1], v[2]) for v in valid])
        coords_rad = np.radians(coords)
        eps_rad = config.gps_cluster_eps_km / 6371.0

        labels = DBSCAN(
            eps=eps_rad,
            min_samples=config.gps_cluster_min_samples,
            algorithm="ball_tree",
            metric="haversine",
        ).fit_predict(coords_rad)

        clear_gps_clusters(corpus_conn)

        geolabels = _load_geolabels(corpus_conn)
        unique_labels = sorted(set(labels) - {-1})

        cluster_db_ids: dict[int, int] = {}
        total = len(files)

        for lbl in unique_labels:
            if cancel_event.is_set():
                break
            mask = labels == lbl
            member_indices = [i for i in range(len(files)) if mask[i]]
            member_ids = [files[i]["id"] for i in member_indices]
            member_coords = coords[mask]
            centroid_lat = float(member_coords[:, 0].mean())
            centroid_lon = float(member_coords[:, 1].mean())
            label = _name_cluster(member_ids, geolabels, centroid_lat, centroid_lon)

            cur = corpus_conn.execute(
                "INSERT INTO gps_clusters"
                " (label, centroid_lat, centroid_lon, file_count, eps_km, min_samples)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (label, centroid_lat, centroid_lon, len(member_ids),
                 config.gps_cluster_eps_km, config.gps_cluster_min_samples),
            )
            cluster_db_ids[lbl] = cur.lastrowid

        corpus_conn.commit()

        # Pre-fetch centroids to avoid repeated queries
        centroids: dict[int, tuple[float, float]] = {}
        for lbl, db_id in cluster_db_ids.items():
            row = corpus_conn.execute(
                "SELECT centroid_lat, centroid_lon FROM gps_clusters WHERE id=?", (db_id,)
            ).fetchone()
            centroids[lbl] = (row["centroid_lat"], row["centroid_lon"])

        assigned = noise = 0
        for i, file_row in enumerate(files):
            if cancel_event.is_set():
                break
            progress.update(i, total, Path(file_row["path"]).name)

            lbl = int(labels[i])
            if lbl == -1:
                corpus_conn.execute(
                    "INSERT OR REPLACE INTO file_gps_cluster_assignments"
                    " (file_id, cluster_id, distance_m) VALUES (?, NULL, NULL)",
                    (file_row["id"],),
                )
                noise += 1
            else:
                db_id = cluster_db_ids[lbl]
                c_lat, c_lon = centroids[lbl]
                dist = _haversine_m(parse_gps_value(file_row["lat"]), parse_gps_value(file_row["lon"]), c_lat, c_lon)
                corpus_conn.execute(
                    "INSERT OR REPLACE INTO file_gps_cluster_assignments"
                    " (file_id, cluster_id, distance_m) VALUES (?, ?, ?)",
                    (file_row["id"], db_id, dist),
                )
                assigned += 1

        corpus_conn.commit()

        if export and not cancel_event.is_set():
            export_dir = kb_folder / "export"
            export_dir.mkdir(parents=True, exist_ok=True)
            _write_gps_clusters(export_dir, corpus_conn)

        progress.done()
        return {
            "clusters": len(unique_labels),
            "assigned": assigned,
            "noise": noise,
        }
    finally:
        corpus_conn.close()


def _write_gps_clusters(export_dir: Path, corpus_conn) -> None:
    import csv
    from src.db.corpus import get_gps_cluster_assignments_for_export

    rows = get_gps_cluster_assignments_for_export(corpus_conn)
    if not rows:
        return
    with open(export_dir / "gps_clusters.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["path", "cluster_id", "cluster_label", "centroid_lat", "centroid_lon", "distance_m"],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "path": r["path"],
                "cluster_id": r["cluster_id"],
                "cluster_label": r["cluster_label"] or "",
                "centroid_lat": r["centroid_lat"],
                "centroid_lon": r["centroid_lon"],
                "distance_m": r["distance_m"],
            })
