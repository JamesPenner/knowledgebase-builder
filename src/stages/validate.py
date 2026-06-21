"""Corpus file validation — checks existence and content integrity."""
import hashlib
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _classify_file(
    file_row,
    sha256_to_paths: dict[str, list[tuple[int, str]]],
) -> tuple[str, str | None]:
    """Return (status, detail) for one corpus file.

    sha256_to_paths maps sha256 → [(file_id, path), ...] for all corpus files,
    used to detect moves without a full filesystem scan.
    """
    path = Path(file_row["path"])
    stored_hash: str | None = file_row["sha256"]

    if path.exists():
        if not stored_hash:
            return "ok", None
        try:
            current_hash = _sha256_file(path)
        except OSError:
            return "missing", None
        if current_hash == stored_hash:
            return "ok", None
        return "changed", current_hash

    # File not found at recorded path — check for a move
    if stored_hash and stored_hash in sha256_to_paths:
        this_id = file_row["id"]
        for other_id, other_path in sha256_to_paths[stored_hash]:
            if other_id != this_id and Path(other_path).exists():
                return "moved", other_path

    return "missing", None


def run_validate(
    corpus_path: Path,
    kb_folder: Path,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    export: bool = False,
) -> dict:
    from src.db.corpus import open_corpus

    corpus_conn = open_corpus(corpus_path)
    try:
        files = corpus_conn.execute(
            "SELECT id, path, sha256 FROM files ORDER BY id"
        ).fetchall()

        sha256_to_paths: dict[str, list[tuple[int, str]]] = {}
        for f in files:
            if f["sha256"]:
                sha256_to_paths.setdefault(f["sha256"], []).append((f["id"], f["path"]))

        total = len(files)
        ok = changed = moved = missing = 0
        run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        cur = corpus_conn.execute(
            "INSERT INTO validation_runs (run_at, files_checked, ok_count, changed_count, moved_count, missing_count)"
            " VALUES (?, 0, 0, 0, 0, 0)",
            (run_at,),
        )
        run_id = cur.lastrowid
        corpus_conn.commit()

        for i, file_row in enumerate(files):
            if cancel_event.is_set():
                break
            progress.update(i, total, file_row["path"])

            status, detail = _classify_file(file_row, sha256_to_paths)
            corpus_conn.execute(
                "INSERT INTO validation_results (run_id, file_id, status, detail) VALUES (?, ?, ?, ?)",
                (run_id, file_row["id"], status, detail),
            )
            if status == "ok":
                ok += 1
            elif status == "changed":
                changed += 1
            elif status == "moved":
                moved += 1
            else:
                missing += 1

        files_checked = ok + changed + moved + missing
        corpus_conn.execute(
            "UPDATE validation_runs"
            " SET files_checked=?, ok_count=?, changed_count=?, moved_count=?, missing_count=?"
            " WHERE id=?",
            (files_checked, ok, changed, moved, missing, run_id),
        )
        corpus_conn.commit()

        if export and not cancel_event.is_set():
            export_dir = kb_folder / "export"
            export_dir.mkdir(parents=True, exist_ok=True)
            _write_validation_report(export_dir, corpus_conn)

        progress.done()
        return {
            "run_id": run_id,
            "ok": ok,
            "changed": changed,
            "moved": moved,
            "missing": missing,
        }
    finally:
        corpus_conn.close()


def _write_validation_report(export_dir: Path, corpus_conn) -> None:
    import csv
    from src.db.corpus import get_validation_results_for_export

    rows = get_validation_results_for_export(corpus_conn)
    with open(export_dir / "validation_report.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["path", "status", "detail", "checked_at"])
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "path": r["path"],
                "status": r["status"],
                "detail": r["detail"] or "",
                "checked_at": r["checked_at"],
            })
