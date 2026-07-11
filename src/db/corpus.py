import re
import sqlite3
from pathlib import Path

from src.db.migrations import apply_migrations
from src.db.utils import configure_connection as _configure
from src.pipeline.clusters import ClusterAssignment
from src.pipeline.filter_spec import CorpusFilterSpec


def parse_gps_value(raw: str | None) -> float | None:
    """Convert ExifTool GPS string to signed decimal degrees.

    Handles decimal strings (e.g. '48.3853') and DMS strings produced by
    ExifTool (e.g. '48 deg 23\' 7.18" N', '123 deg 30\' 53.67" W').
    Returns None if the value is absent or unparseable.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    try:
        return float(s)
    except ValueError:
        pass
    m = re.match(r"(\d+)\s+deg\s+(\d+)'\s+([\d.]+)\"\s*([NSEWnsew])?", s)
    if m:
        deg = float(m.group(1))
        mins = float(m.group(2))
        secs = float(m.group(3))
        direction = (m.group(4) or "").upper()
        result = deg + mins / 60.0 + secs / 3600.0
        if direction in ("S", "W"):
            result = -result
        return result
    return None

_MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations" / "corpus"


def open_corpus(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _configure(conn)
    apply_migrations(conn, _MIGRATIONS_DIR)
    return conn


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def add_source(
    conn: sqlite3.Connection,
    path: str,
    file_type: str = "all",
    recursive: bool = True,
    filters_json: dict | None = None,
    incremental: bool = False,
) -> int:
    import json as _json
    filters_str = _json.dumps(filters_json or {})
    cur = conn.execute(
        "INSERT OR IGNORE INTO sources (path, file_type, recursive, filters_json, incremental)"
        " VALUES (?, ?, ?, ?, ?)",
        (path, file_type, int(recursive), filters_str, int(incremental)),
    )
    conn.commit()
    if cur.lastrowid:
        return cur.lastrowid
    row = conn.execute("SELECT id FROM sources WHERE path = ?", (path,)).fetchone()
    return row["id"]


# Tables that store per-file derived data and must be cleared when files are removed.
# NOTE: file_set_members is intentionally excluded here — set membership is a user-defined
# grouping that should survive source removal. It is only cleared by reset_corpus_files.
_CORPUS_DERIVED_TABLES = [
    "face_cluster_members",
    "file_voice_segments",
    "file_voice_embeddings",
    "file_face_regions",
    "file_geolabels",
    "file_gps_masks",
    "file_gps_cluster_assignments",
    "validation_results",
    "file_temporal_fields",
    "file_derived_tags",
    "file_entity_matches",
    "file_quality",
    "file_aesthetic",
    "file_hashes",
    "file_captured_fields",
    "file_exif",
    "file_metadata_fields",
    "file_metadata_keywords",
    "descriptions",
    "video_frames",
    "candidates",
    "transcriptions",
    "transcript_segments",
    "retag_output",
    "writeback_log",
    "file_summaries",
    "file_location_labels",
]


def remove_source(conn: sqlite3.Connection, source_id: int, cascade: bool = False) -> int:
    """Soft-delete or cascade-delete a source.

    cascade=False: set removed_at on the source row only; returns 0.
    cascade=True:  delete all files for the source (and their dependent rows)
                   inside a single transaction; returns count of files deleted.
    """
    if not cascade:
        conn.execute(
            "UPDATE sources SET removed_at = datetime('now') WHERE id = ?",
            (source_id,),
        )
        conn.commit()
        return 0

    file_id_rows = conn.execute(
        "SELECT id FROM files WHERE source_id = ?", (source_id,)
    ).fetchall()
    if not file_id_rows:
        conn.execute(
            "UPDATE sources SET removed_at = datetime('now') WHERE id = ?",
            (source_id,),
        )
        conn.commit()
        return 0

    file_ids = [r["id"] for r in file_id_rows]
    placeholders = ",".join("?" * len(file_ids))

    for tbl in _CORPUS_DERIVED_TABLES:
        conn.execute(
            f"DELETE FROM {tbl} WHERE file_id IN ({placeholders})", file_ids
        )

    conn.execute(
        f"DELETE FROM files WHERE id IN ({placeholders})", file_ids
    )
    conn.execute(
        "UPDATE sources SET removed_at = datetime('now') WHERE id = ?",
        (source_id,),
    )
    conn.commit()
    return len(file_ids)


def get_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sources WHERE removed_at IS NULL ORDER BY id"
    ).fetchall()


def update_source(
    conn: sqlite3.Connection,
    source_id: int,
    file_type: str,
    recursive: bool,
    filters_json: dict,
    incremental: bool = False,
) -> bool:
    import json as _json
    cur = conn.execute(
        "UPDATE sources SET file_type=?, recursive=?, filters_json=?, incremental=?"
        " WHERE id=? AND removed_at IS NULL",
        (file_type, int(recursive), _json.dumps(filters_json), int(incremental), source_id),
    )
    conn.commit()
    return cur.rowcount > 0


def reset_corpus_files(conn: sqlite3.Connection) -> int:
    """Delete all corpus files and their derived data. Returns the file count cleared."""
    count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    # Also clear file_set_members — a full reset removes all user-defined groupings too.
    for tbl in ["file_set_members"] + _CORPUS_DERIVED_TABLES:
        conn.execute(f"DELETE FROM {tbl}")
    conn.execute("DELETE FROM files")
    conn.execute(
        "UPDATE sources SET last_ingested_at = NULL, file_count_ingested = 0"
        " WHERE removed_at IS NULL"
    )
    conn.commit()
    return count


# ---------------------------------------------------------------------------
# File sets
# ---------------------------------------------------------------------------

def create_file_set(
    conn: sqlite3.Connection,
    name: str,
    description: str,
    spec: CorpusFilterSpec,
) -> int:
    cur = conn.execute(
        """INSERT INTO file_sets
           (name, description, source_id, folder_prefix, file_type,
            date_from, date_to, name_pattern, criteria_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, description, spec.source_id, spec.folder_prefix, spec.file_type,
         spec.date_from, spec.date_to, spec.name_pattern, spec.summary()),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _set_row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    spec = CorpusFilterSpec(
        source_id=row["source_id"],
        folder_prefix=row["folder_prefix"],
        file_type=row["file_type"],
        date_from=row["date_from"],
        date_to=row["date_to"],
        name_pattern=row["name_pattern"],
    )
    frag, params = spec.to_sql_fragment()
    count = conn.execute(
        f"SELECT COUNT(*) FROM files f WHERE 1=1{frag}", params
    ).fetchone()[0]
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "created_at": row["created_at"],
        "source_id": row["source_id"],
        "folder_prefix": row["folder_prefix"],
        "file_type": row["file_type"],
        "date_from": row["date_from"],
        "date_to": row["date_to"],
        "name_pattern": row["name_pattern"],
        "criteria_summary": row["criteria_summary"],
        "file_count": count,
    }


def get_file_sets(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT id, name, description, created_at, source_id, folder_prefix,
                  file_type, date_from, date_to, name_pattern, criteria_summary
           FROM file_sets ORDER BY created_at DESC"""
    ).fetchall()
    return [_set_row_to_dict(conn, r) for r in rows]


def get_file_set(conn: sqlite3.Connection, set_id: int) -> dict | None:
    row = conn.execute(
        """SELECT id, name, description, created_at, source_id, folder_prefix,
                  file_type, date_from, date_to, name_pattern, criteria_summary
           FROM file_sets WHERE id = ?""",
        (set_id,),
    ).fetchone()
    if row is None:
        return None
    return _set_row_to_dict(conn, row)


def delete_file_set(conn: sqlite3.Connection, set_id: int) -> None:
    conn.execute("DELETE FROM file_sets WHERE id = ?", (set_id,))
    conn.commit()


def resolve_set_as_filter(conn: sqlite3.Connection, set_id: int) -> CorpusFilterSpec:
    row = conn.execute(
        "SELECT source_id, folder_prefix, file_type, date_from, date_to, name_pattern "
        "FROM file_sets WHERE id = ?",
        (set_id,),
    ).fetchone()
    if row is None:
        return CorpusFilterSpec()
    return CorpusFilterSpec(
        source_id=row["source_id"],
        folder_prefix=row["folder_prefix"],
        file_type=row["file_type"],
        date_from=row["date_from"],
        date_to=row["date_to"],
        name_pattern=row["name_pattern"],
    )


def get_distinct_folders(
    conn: sqlite3.Connection,
    source_id: int | None = None,
) -> list[str]:
    """Return sorted distinct parent-directory paths for all ingested files."""
    if source_id is not None:
        rows = conn.execute(
            "SELECT path FROM files WHERE source_id = ?", (source_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT path FROM files").fetchall()
    folders = sorted({Path(r["path"]).parent.as_posix() for r in rows})
    return folders


def count_files_matching(conn: sqlite3.Connection, spec: CorpusFilterSpec) -> int:
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"SELECT COUNT(*) FROM files f WHERE 1=1{frag}", params
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def upsert_file(
    conn: sqlite3.Connection,
    source_id: int,
    path: str,
    filename: str,
    ext: str,
    file_type: str,
    file_size: int,
    mtime: float,
) -> int:
    conn.execute(
        """
        INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            file_size = excluded.file_size,
            mtime     = excluded.mtime,
            ext       = excluded.ext,
            file_type = excluded.file_type
        """,
        (source_id, path, filename, ext, file_type, file_size, mtime),
    )
    row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
    return row["id"]


def get_all_file_paths(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT path FROM files").fetchall()
    return [r["path"] for r in rows]


def get_files_for_analyse(
    conn: sqlite3.Connection,
    *,
    scope: "CorpusFilterSpec | None" = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"SELECT f.id, f.path, f.filename, f.ext FROM files f WHERE 1=1{frag} ORDER BY f.id",
        params,
    ).fetchall()


def get_files_for_normalize(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, path, filename FROM files ORDER BY id").fetchall()


def upsert_captured_field(conn: sqlite3.Connection, file_id: int, field_name: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO file_captured_fields (file_id, field_name, value)
        VALUES (?, ?, ?)
        ON CONFLICT(file_id, field_name) DO UPDATE SET value = excluded.value
        """,
        (file_id, field_name, value),
    )


def update_filename_normalized(conn: sqlite3.Connection, file_id: int, value: str) -> None:
    conn.execute("UPDATE files SET filename_normalized = ? WHERE id = ?", (value, file_id))


# ---------------------------------------------------------------------------
# Analyse tokens
# ---------------------------------------------------------------------------

def upsert_analyse_token(
    conn: sqlite3.Connection,
    token: str,
    pattern_class: str,
    semantic_type: str,
    frequency: int,
    file_count: int,
    proposed_action: str,
    proposed_extract_as: str,
    is_cross_source: bool,
    depth_position: int,
) -> None:
    conn.execute(
        """
        INSERT INTO analyse_tokens
            (token, pattern_class, semantic_type, frequency, file_count,
             proposed_action, proposed_extract_as, is_cross_source, depth_position)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(token) DO UPDATE SET
            pattern_class       = excluded.pattern_class,
            semantic_type       = excluded.semantic_type,
            frequency           = excluded.frequency,
            file_count          = excluded.file_count,
            proposed_action     = excluded.proposed_action,
            proposed_extract_as = excluded.proposed_extract_as,
            is_cross_source     = excluded.is_cross_source,
            depth_position      = excluded.depth_position
        """,
        (
            token, pattern_class, semantic_type, frequency, file_count,
            proposed_action, proposed_extract_as, int(is_cross_source), depth_position,
        ),
    )


def delete_stale_analyse_tokens(conn: sqlite3.Connection, current_tokens: set[str]) -> int:
    """Remove tokens that no longer appear in the corpus. Returns count deleted."""
    if not current_tokens:
        conn.execute("DELETE FROM analyse_tokens")
        conn.commit()
        return 0
    placeholders = ",".join("?" * len(current_tokens))
    cur = conn.execute(
        f"DELETE FROM analyse_tokens WHERE token NOT IN ({placeholders})",
        list(current_tokens),
    )
    conn.commit()
    return cur.rowcount


def get_pending_analyse_tokens(
    conn: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM analyse_tokens
        WHERE status = 'pending'
        ORDER BY file_count DESC, frequency DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()


def get_analyse_token_counts(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) as total, SUM(CASE WHEN status='decided' THEN 1 ELSE 0 END) as reviewed FROM analyse_tokens"
    ).fetchone()
    return {"total": row["total"] or 0, "reviewed": row["reviewed"] or 0}


def set_token_decided(conn: sqlite3.Connection, token_id: int) -> None:
    conn.execute("UPDATE analyse_tokens SET status='decided' WHERE id=?", (token_id,))
    conn.commit()


def get_all_pending_tokens(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, token FROM analyse_tokens WHERE status='pending' ORDER BY id"
    ).fetchall()


def mark_analyse_tokens_decided(conn: sqlite3.Connection, ids: list[int]) -> None:
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE analyse_tokens SET status='decided' WHERE id IN ({placeholders})",
        ids,
    )
    conn.commit()


def set_all_pending_decided(conn: sqlite3.Connection) -> int:
    cur = conn.execute("UPDATE analyse_tokens SET status='decided' WHERE status='pending'")
    conn.commit()
    return cur.rowcount


def set_token_pending(conn: sqlite3.Connection, token_id: int) -> None:
    conn.execute("UPDATE analyse_tokens SET status='pending' WHERE id=?", (token_id,))
    conn.commit()


def get_token_by_value(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM analyse_tokens WHERE token=?", (token,)).fetchone()


# ---------------------------------------------------------------------------
# Pipeline checkpoints
# ---------------------------------------------------------------------------

def update_pipeline_checkpoint(
    conn: sqlite3.Connection,
    stage: str,
    files_processed: int,
    files_skipped: int = 0,
    errors: int = 0,
    duration_seconds: float = 0.0,
) -> None:
    conn.execute(
        """
        INSERT INTO pipeline_checkpoints
            (stage, last_run_at, files_processed, files_skipped, errors, duration_seconds)
        VALUES (?, datetime('now'), ?, ?, ?, ?)
        ON CONFLICT(stage) DO UPDATE SET
            last_run_at      = datetime('now'),
            files_processed  = excluded.files_processed,
            files_skipped    = excluded.files_skipped,
            errors           = excluded.errors,
            duration_seconds = excluded.duration_seconds
        """,
        (stage, files_processed, files_skipped, errors, duration_seconds),
    )
    conn.commit()


def get_completed_stages(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT stage FROM pipeline_checkpoints").fetchall()
    return {r["stage"] for r in rows}


def get_pipeline_checkpoints(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pipeline_checkpoints ORDER BY stage"
    ).fetchall()


_STAGE_COUNT_QUERIES: dict[str, str] = {
    "extract_meta":      "SELECT COUNT(*) FROM file_exif",
    "extract_fields":    "SELECT COUNT(DISTINCT file_id) FROM file_metadata_fields",
    "hash":              "SELECT COUNT(*) FROM files WHERE sha256 IS NOT NULL",
    "temporal":          "SELECT COUNT(DISTINCT file_id) FROM file_temporal_fields",
    "entity_match":      "SELECT COUNT(DISTINCT file_id) FROM file_entity_matches",
    "classify":          "SELECT COUNT(DISTINCT file_id) FROM file_derived_tags",
    "quality":           "SELECT COUNT(*) FROM file_quality",
    "aesthetic":         "SELECT COUNT(*) FROM file_aesthetic",
    "geo_meta":          "SELECT COUNT(DISTINCT file_id) FROM file_location_labels",
    "geolocate":         "SELECT COUNT(DISTINCT file_id) FROM file_geolabels",
    "face":              "SELECT COUNT(DISTINCT file_id) FROM file_face_regions",
    "face_meta":         "SELECT COUNT(DISTINCT file_id) FROM file_face_regions",
    "voice":             "SELECT COUNT(DISTINCT file_id) FROM file_voice_embeddings",
    "voice_diarize":     "SELECT COUNT(DISTINCT file_id) FROM file_voice_segments",
    "attribute_speakers":"SELECT COUNT(DISTINCT file_id) FROM file_voice_segments WHERE speaker_label IS NOT NULL",
    "retag":             "SELECT COUNT(*) FROM retag_output",
    "writeback":         "SELECT COUNT(DISTINCT file_id) FROM writeback_log",
}


def get_pipeline_stage_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Return accurate cumulative file counts per stage by querying output tables.

    Only covers stages where the checkpoint's files_processed can be stale after a
    resume run. Stages not listed here (ingest, analyse, normalize, extract_fields,
    describe, transcribe, etc.) are either always-cumulative or handled separately.
    """
    counts: dict[str, int] = {}
    for stage, sql in _STAGE_COUNT_QUERIES.items():
        try:
            row = conn.execute(sql).fetchone()
            counts[stage] = row[0] if row else 0
        except Exception:
            pass
    return counts


# ---------------------------------------------------------------------------
# File EXIF metadata
# ---------------------------------------------------------------------------

def upsert_file_exif(conn: sqlite3.Connection, file_id: int, metadata_json: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO file_exif (file_id, metadata_json, extracted_at) VALUES (?, ?, datetime('now'))",
        (file_id, metadata_json),
    )


def get_files_without_exif(
    conn: sqlite3.Connection,
    *,
    scope: "CorpusFilterSpec | None" = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"""
        SELECT f.id, f.path FROM files f
        LEFT JOIN file_exif e ON e.file_id = f.id
        WHERE e.file_id IS NULL{frag} ORDER BY f.id
        """,
        params,
    ).fetchall()


def get_files_with_exif(
    conn: sqlite3.Connection,
    *,
    scope: "CorpusFilterSpec | None" = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"""
        SELECT f.id, f.path, e.metadata_json FROM files f
        JOIN file_exif e ON e.file_id = f.id WHERE 1=1{frag} ORDER BY f.id
        """,
        params,
    ).fetchall()


def reset_file_exif(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM file_exif")
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Extracted metadata fields
# ---------------------------------------------------------------------------

def upsert_metadata_field(
    conn: sqlite3.Connection,
    file_id: int,
    canonical_name: str,
    raw_field_name: str,
    value: str,
    value_type: str,
) -> None:
    conn.execute(
        """
        INSERT INTO file_metadata_fields
            (file_id, canonical_name, raw_field_name, value, value_type, extracted_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(file_id, canonical_name) DO UPDATE SET
            raw_field_name = excluded.raw_field_name,
            value          = excluded.value,
            value_type     = excluded.value_type,
            extracted_at   = datetime('now')
        """,
        (file_id, canonical_name, raw_field_name, value, value_type),
    )


def upsert_metadata_keyword(
    conn: sqlite3.Connection,
    file_id: int,
    canonical_name: str,
    keyword: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO file_metadata_keywords (file_id, canonical_name, keyword)
        VALUES (?, ?, ?)
        """,
        (file_id, canonical_name, keyword),
    )


def get_files_without_fields(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.id, f.path FROM files f
        JOIN file_exif e ON e.file_id = f.id
        LEFT JOIN file_metadata_fields m ON m.file_id = f.id
        WHERE m.file_id IS NULL ORDER BY f.id
        """
    ).fetchall()


def reset_file_fields(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM file_metadata_keywords")
    cur = conn.execute("DELETE FROM file_metadata_fields")
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# File hashes
# ---------------------------------------------------------------------------

def reset_file_hashes(conn: sqlite3.Connection) -> int:
    conn.execute("UPDATE files SET sha256 = NULL")
    cur = conn.execute("DELETE FROM file_hashes")
    conn.commit()
    return cur.rowcount


def get_files_without_hash(
    conn: sqlite3.Connection,
    *,
    scope: "CorpusFilterSpec | None" = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"SELECT f.id, f.path, f.file_type FROM files f WHERE f.sha256 IS NULL{frag} ORDER BY f.id",
        params,
    ).fetchall()


def get_videos_without_frame_hash(
    conn: sqlite3.Connection,
    *,
    scope: "CorpusFilterSpec | None" = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"""
        SELECT f.id, f.path FROM files f
        LEFT JOIN file_hashes fh ON fh.file_id = f.id
        WHERE f.file_type = 'video'
          AND (fh.video_collage_phash IS NULL){frag}
        ORDER BY f.id
        """,
        params,
    ).fetchall()


def update_file_sha256(conn: sqlite3.Connection, file_id: int, sha256: str) -> None:
    conn.execute("UPDATE files SET sha256 = ? WHERE id = ?", (sha256, file_id))


def upsert_file_hash(
    conn: sqlite3.Connection,
    file_id: int,
    sha256_content: str,
    phash: str,
    dhash: str,
) -> None:
    conn.execute(
        """
        INSERT INTO file_hashes
            (file_id, sha256_content, phash, dhash, hashed_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(file_id) DO UPDATE SET
            sha256_content = excluded.sha256_content,
            phash          = excluded.phash,
            dhash          = excluded.dhash,
            hashed_at      = datetime('now')
        """,
        (file_id, sha256_content, phash, dhash),
    )


def upsert_video_hash(
    conn: sqlite3.Connection,
    file_id: int,
    collage_phash: str | None,
    frame_phashes_json: str,
) -> None:
    conn.execute(
        """
        INSERT INTO file_hashes
            (file_id, video_collage_phash, video_frame_phashes, hashed_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(file_id) DO UPDATE SET
            video_collage_phash  = excluded.video_collage_phash,
            video_frame_phashes  = excluded.video_frame_phashes,
            hashed_at            = datetime('now')
        """,
        (file_id, collage_phash, frame_phashes_json),
    )


_CAPTURE_PATTERNS: dict[tuple[str, str], str | None] = {
    ("6digit_numeric", "date"):      r"^\d{6}$",
    ("6digit_numeric", "time"):      r"^\d{6}$",
    ("8digit_numeric", "date"):      r"^\d{8}$",
    ("sequential",     "sequential"): None,
    ("route_code",     "code"):      r"^[A-Za-z]{1,4}-\d+$",
    ("camelcase",      "compound"):  None,
    ("word",           "word"):      None,
}
_CLASS_LABEL: dict[str, str] = {
    "6digit_numeric": "6-digit numeric",
    "8digit_numeric": "8-digit numeric",
    "sequential":     "Short sequential",
    "route_code":     "Route codes",
    "camelcase":      "CamelCase compound terms",
    "word":           "Words",
    "ngram":          "Filename compound terms",
}
_SEMANTIC_LABEL: dict[str, str] = {
    "date":         "Likely dates",
    "time":         "Likely times",
    "sequential":   "Sequential counters",
    "code":         "Codes",
    "compound":     "Compound terms",
    "word":         "Words",
    "unclassified": "Unclassified",
}
_SEMANTIC_VALUE_TYPE: dict[str, str] = {
    "date": "date",
    "time": "time",
    "code": "code",
}


# ---------------------------------------------------------------------------
# Entity matching
# ---------------------------------------------------------------------------

def get_files_with_gps(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.id, f.path,
               lat.value AS lat,
               lon.value AS lon
        FROM files f
        JOIN file_metadata_fields lat ON lat.file_id = f.id AND lat.canonical_name = 'exif_gps_lat'
        JOIN file_metadata_fields lon ON lon.file_id = f.id AND lon.canonical_name = 'exif_gps_lon'
        WHERE lat.value IS NOT NULL AND lon.value IS NOT NULL
        ORDER BY f.id
        """
    ).fetchall()


def get_files_for_text_match(
    conn: sqlite3.Connection,
    *,
    scope: "CorpusFilterSpec | None" = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"""
        SELECT DISTINCT f.id, f.path FROM files f
        WHERE (
            EXISTS (SELECT 1 FROM file_metadata_fields WHERE file_id = f.id)
            OR EXISTS (SELECT 1 FROM file_metadata_keywords WHERE file_id = f.id)
            OR EXISTS (SELECT 1 FROM file_captured_fields WHERE file_id = f.id)
        ){frag}
        ORDER BY f.id
        """,
        params,
    ).fetchall()


def get_enrichment_text_for_file(conn: sqlite3.Connection, file_id: int) -> str:
    parts: list[str] = []
    for row in conn.execute(
        "SELECT value, value_type FROM file_metadata_fields"
        " WHERE file_id = ? AND value IS NOT NULL",
        (file_id,),
    ).fetchall():
        vt = row["value_type"] or "str"
        if vt not in ("float", "int"):
            parts.append(row["value"])
    for row in conn.execute(
        "SELECT COALESCE(normalized_keyword, keyword) AS kw"
        " FROM file_metadata_keywords WHERE file_id = ? AND keyword IS NOT NULL",
        (file_id,),
    ).fetchall():
        if row["kw"]:
            parts.append(row["kw"])
    for row in conn.execute(
        "SELECT value FROM file_captured_fields WHERE file_id = ? AND value IS NOT NULL",
        (file_id,),
    ).fetchall():
        parts.append(row["value"])
    return " ".join(p for p in parts if p)


def upsert_entity_match(
    conn: sqlite3.Connection,
    file_id: int,
    table_name: str,
    matched_value: str,
    match_source: str,
    payload_json: str,
) -> None:
    conn.execute(
        """
        INSERT INTO file_entity_matches
            (file_id, table_name, matched_value, match_source, payload_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(file_id, table_name, matched_value) DO UPDATE SET
            match_source = excluded.match_source,
            payload_json = excluded.payload_json,
            stale        = 0,
            matched_at   = datetime('now')
        """,
        (file_id, table_name, matched_value, match_source, payload_json),
    )


def get_entity_matches_for_file(
    conn: sqlite3.Connection, file_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM file_entity_matches WHERE file_id = ? AND stale = 0",
        (file_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Classify output
# ---------------------------------------------------------------------------

def get_files_for_classify(
    conn: sqlite3.Connection,
    *,
    scope: "CorpusFilterSpec | None" = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"SELECT f.id, f.path FROM files f WHERE 1=1{frag} ORDER BY f.id",
        params,
    ).fetchall()


def get_fields_for_classify(conn: sqlite3.Connection, file_id: int) -> dict[str, str]:
    fields: dict[str, str] = {}

    for row in conn.execute(
        "SELECT field_name, value FROM file_captured_fields WHERE file_id = ?",
        (file_id,),
    ).fetchall():
        if row["value"] is not None:
            fields[row["field_name"]] = row["value"]

    for row in conn.execute(
        "SELECT canonical_name, value FROM file_metadata_fields"
        " WHERE file_id = ? AND value IS NOT NULL",
        (file_id,),
    ).fetchall():
        if row["canonical_name"] not in fields:
            fields[row["canonical_name"]] = row["value"]

    # file_format from files.ext
    frow = conn.execute("SELECT ext FROM files WHERE id = ?", (file_id,)).fetchone()
    if frow and frow["ext"]:
        fields["file_format"] = frow["ext"].lower().lstrip(".")

    # gps_present derived from exif_gps_lat
    if fields.get("exif_gps_lat"):
        fields["gps_present"] = "true"

    # hour_of_day derived from exif_date_taken (supports "2023-12-25T14:30:00" and "2023-12-25 14:30:00")
    date_taken = fields.get("exif_date_taken", "")
    if date_taken and len(date_taken) >= 13:
        sep = date_taken[10]
        if sep in ("T", " "):
            try:
                fields["hour_of_day"] = str(int(date_taken[11:13]))
            except ValueError:
                pass

    # flash_fired derived from EXIF flash bitmask (bit 0 = fired)
    flash_raw = fields.get("flash")
    if flash_raw is not None:
        try:
            fields["flash_fired"] = "1" if (int(float(flash_raw)) & 1) else "0"
        except (ValueError, TypeError):
            pass

    # aspect_ratio derived from exif_width / exif_height
    try:
        w = float(fields.get("exif_width") or 0)
        h = float(fields.get("exif_height") or 0)
        if w > 0 and h > 0:
            fields["aspect_ratio"] = str(w / h)
    except (ValueError, TypeError):
        pass

    # quality metrics (luminance_std_dev, saturation_mean, dominant_hue may be NULL on old rows)
    qrow = conn.execute(
        "SELECT exposure, highlights, shadows, luminance_std_dev, saturation_mean, dominant_hue"
        " FROM file_quality WHERE file_id = ?",
        (file_id,),
    ).fetchone()
    if qrow:
        for col in ("exposure", "highlights", "shadows",
                    "luminance_std_dev", "saturation_mean", "dominant_hue"):
            val = qrow[col]
            if val is not None:
                fields[col] = str(val)

    return fields


def upsert_derived_tag(
    conn: sqlite3.Connection,
    file_id: int,
    tag: str,
    category: str,
    source: str,
    rule_id: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO file_derived_tags (file_id, tag, category, source, rule_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(file_id, tag, category) DO UPDATE SET
            source     = excluded.source,
            rule_id    = excluded.rule_id,
            derived_at = datetime('now')
        """,
        (file_id, tag, category, source, rule_id),
    )


def upsert_gps_proposal(
    conn: sqlite3.Connection,
    file_id: int,
    location_name: str,
    proposed_lat: float,
    proposed_lon: float,
    threshold_m: float | None,
    source_text: str,
) -> None:
    conn.execute(
        """
        INSERT INTO gps_proposals
            (file_id, location_name, proposed_lat, proposed_lon, threshold_m, source_text)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id, location_name) DO UPDATE SET
            proposed_lat = excluded.proposed_lat,
            proposed_lon = excluded.proposed_lon,
            threshold_m  = excluded.threshold_m,
            source_text  = excluded.source_text,
            proposed_at  = datetime('now')
        """,
        (file_id, location_name, proposed_lat, proposed_lon, threshold_m, source_text),
    )


def upsert_candidate(
    conn: sqlite3.Connection,
    file_id: int | None,
    term: str,
    source: str,
    cluster_id: str | None = None,
    notes: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO candidates (file_id, term, source, cluster_id, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (file_id, term, source, cluster_id, notes),
    )


def get_pending_candidates(
    conn: sqlite3.Connection,
    limit: int = 50,
    offset: int = 0,
    source_filter: str | None = None,
    sort_by: str = "file_count",
    sort_order: str = "desc",
) -> list[sqlite3.Row]:
    # Group by term so each unique term appears once regardless of how many files
    # generated it (level_a produces one row per file per term).
    _SORT_COLS = {"term": "term", "file_count": "file_count"}
    col = _SORT_COLS.get(sort_by, "file_count")
    direction = "DESC" if sort_order.lower() == "desc" else "ASC"
    base_where = "WHERE status='pending' AND source=?" if source_filter else "WHERE status='pending'"
    params: tuple = (source_filter, limit, offset) if source_filter else (limit, offset)
    return conn.execute(
        f"""
        SELECT MIN(id) as id, term,
               MIN(source) as source, MIN(cluster_id) as cluster_id,
               MIN(notes) as notes, 'pending' as status, MIN(corrected_to) as corrected_to,
               COUNT(DISTINCT file_id) as file_count
        FROM candidates
        {base_where}
        GROUP BY term
        ORDER BY {col} {direction}
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()


def get_candidate_counts(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT
          COUNT(DISTINCT term) as total,
          COUNT(DISTINCT CASE WHEN status='pending'  THEN term END) as pending,
          COUNT(DISTINCT CASE WHEN status='accepted' THEN term END) as accepted,
          COUNT(DISTINCT CASE WHEN status='rejected' OR status='corrected' THEN term END) as rejected
        FROM candidates
        """
    ).fetchone()
    return {
        "total":    row["total"]    or 0,
        "pending":  row["pending"]  or 0,
        "accepted": row["accepted"] or 0,
        "rejected": row["rejected"] or 0,
    }


def get_decided_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return one row per decided (non-pending) term, most recent status wins."""
    return conn.execute(
        """
        SELECT term,
               MIN(status) as status,
               MIN(corrected_to) as corrected_to
        FROM candidates
        WHERE status != 'pending'
        GROUP BY term
        ORDER BY term ASC
        """
    ).fetchall()


def has_level_b_clusters(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM candidates WHERE source='level_b' AND status='pending'"
    ).fetchone()
    return (row[0] or 0) > 0


def set_candidate_status(
    conn: sqlite3.Connection,
    candidate_id: int,
    status: str,
    corrected_to: str | None = None,
) -> None:
    conn.execute(
        "UPDATE candidates SET status=?, corrected_to=? WHERE id=?",
        (status, corrected_to, candidate_id),
    )


def set_term_candidates_status(
    conn: sqlite3.Connection,
    term: str,
    status: str,
    corrected_to: str | None = None,
) -> None:
    """Update ALL pending candidate rows for a term to a new status."""
    conn.execute(
        "UPDATE candidates SET status=?, corrected_to=? WHERE term=? AND status='pending'",
        (status, corrected_to, term),
    )


def delete_pending_candidates(
    conn: sqlite3.Connection,
    source_filter: str | None = None,
) -> int:
    if source_filter:
        cur = conn.execute(
            "DELETE FROM candidates WHERE status='pending' AND source=?",
            (source_filter,),
        )
    else:
        cur = conn.execute("DELETE FROM candidates WHERE status='pending'")
    return cur.rowcount


def iter_file_term_sets(conn: sqlite3.Connection):
    """Yield one set[str] of Level A candidate terms per file_id (streaming)."""
    cur = conn.execute(
        """
        SELECT file_id, term FROM candidates
        WHERE source='level_a' AND status='pending' AND file_id IS NOT NULL
        ORDER BY file_id
        """
    )
    current_file: int | None = None
    current_terms: set[str] = set()
    for row in cur:
        fid = row["file_id"]
        if fid != current_file:
            if current_file is not None:
                yield current_terms
            current_file = fid
            current_terms = set()
        current_terms.add(row["term"])
    if current_file is not None:
        yield current_terms


def get_candidate_term_file_count(conn: sqlite3.Connection, term: str) -> int:
    row = conn.execute(
        "SELECT COUNT(DISTINCT file_id) as cnt FROM candidates WHERE term=? AND source='level_a'",
        (term,),
    ).fetchone()
    return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# Retag output
# ---------------------------------------------------------------------------

def get_pending_retag_files(
    conn: sqlite3.Connection,
    *,
    scope: CorpusFilterSpec | None = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"""
        SELECT f.id, f.path FROM files f
        LEFT JOIN retag_output r ON r.file_id = f.id
        WHERE (r.file_id IS NULL OR r.retag_status IN ('pending', 'failed', 'skipped'))
          {frag}
        ORDER BY f.id
        """,
        params,
    ).fetchall()


def upsert_retag_output(
    conn: sqlite3.Connection,
    file_id: int,
    tags_json: str,
    refined_description: str | None,
    new_terms_json: str,
    model: str,
    status: str,
) -> None:
    conn.execute(
        """
        INSERT INTO retag_output
            (file_id, tags_json, refined_description, new_terms_proposed_json,
             model, processed_at, retag_status)
        VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
        ON CONFLICT(file_id) DO UPDATE SET
            tags_json               = excluded.tags_json,
            refined_description     = excluded.refined_description,
            new_terms_proposed_json = excluded.new_terms_proposed_json,
            model                   = excluded.model,
            processed_at            = datetime('now'),
            retag_status            = excluded.retag_status
        """,
        (file_id, tags_json, refined_description, new_terms_json, model, status),
    )


def reset_retag_to_pending(conn: sqlite3.Connection) -> int:
    cur = conn.execute("UPDATE retag_output SET retag_status='pending'")
    conn.commit()
    return cur.rowcount


def get_new_terms_candidates(
    conn: sqlite3.Connection,
    kb_conn: sqlite3.Connection,
) -> list[dict]:
    import json as _json
    vocab = {
        r["term"]
        for r in kb_conn.execute("SELECT term FROM vocabulary").fetchall()
    }
    rows = conn.execute(
        "SELECT new_terms_proposed_json FROM retag_output"
        " WHERE retag_status='done' AND new_terms_proposed_json IS NOT NULL"
    ).fetchall()
    term_counts: dict[str, int] = {}
    for row in rows:
        try:
            terms = _json.loads(row["new_terms_proposed_json"]) or []
        except (ValueError, TypeError):
            continue
        for t in terms:
            if t and t not in vocab:
                term_counts[t] = term_counts.get(t, 0) + 1
    return sorted(
        [{"term": t, "file_count": c} for t, c in term_counts.items()],
        key=lambda x: -x["file_count"],
    )


def merge_new_term_into_tags(conn: sqlite3.Connection, term: str) -> int:
    import json as _json
    rows = conn.execute(
        "SELECT file_id, tags_json, new_terms_proposed_json FROM retag_output"
        " WHERE retag_status='done' AND new_terms_proposed_json IS NOT NULL"
    ).fetchall()
    updated = 0
    for row in rows:
        try:
            new_terms = _json.loads(row["new_terms_proposed_json"]) or []
        except (ValueError, TypeError):
            continue
        if term not in new_terms:
            continue
        try:
            tags = _json.loads(row["tags_json"]) if row["tags_json"] else []
        except (ValueError, TypeError):
            tags = []
        if term not in tags:
            tags.append(term)
            conn.execute(
                "UPDATE retag_output SET tags_json=? WHERE file_id=?",
                (_json.dumps(tags), row["file_id"]),
            )
            updated += 1
    if updated:
        conn.commit()
    return updated


# ---------------------------------------------------------------------------
# Write-back tracking
# ---------------------------------------------------------------------------

def get_stale_files_for_writeback(
    conn: sqlite3.Connection,
    current_version: int | None,
) -> list[sqlite3.Row]:
    if current_version is None:
        return []
    return conn.execute(
        """
        SELECT id, path FROM files
        WHERE writeback_kb_version IS NULL OR writeback_kb_version < ?
        ORDER BY id
        """,
        (current_version,),
    ).fetchall()


def update_writeback_kb_version(
    conn: sqlite3.Connection,
    file_ids: list[int],
    version_id: int,
) -> None:
    for fid in file_ids:
        conn.execute(
            "UPDATE files SET writeback_kb_version=? WHERE id=?",
            (version_id, fid),
        )


def log_writeback(
    conn: sqlite3.Connection,
    file_id: int,
    field: str,
    value: str,
    status: str,
) -> None:
    conn.execute(
        "INSERT INTO writeback_log (file_id, field, value, written_at, status)"
        " VALUES (?, ?, ?, datetime('now'), ?)",
        (file_id, field, value, status),
    )


def get_grouped_analyse_tokens(
    conn: sqlite3.Connection,
    limit: int | None = None,
) -> tuple[list[dict], int]:
    """Return pending tokens grouped by pattern_class → semantic_type for the review UI.

    Groups are sorted by total file coverage descending so the most-relevant
    groups appear first.  Returns (groups, total_group_count) so callers can
    determine whether more groups are available.
    """
    from collections import defaultdict

    rows = conn.execute(
        "SELECT * FROM analyse_tokens WHERE status='pending' ORDER BY file_count DESC, frequency DESC"
    ).fetchall()

    class_groups: dict[str, list] = defaultdict(list)
    for row in rows:
        class_groups[row["pattern_class"]].append(row)

    all_groups = []
    for pattern_class, class_rows in class_groups.items():
        type_groups: dict[str, list] = defaultdict(list)
        for row in class_rows:
            type_groups[row["semantic_type"]].append(row)

        subgroups = []
        for semantic_type, type_rows in type_groups.items():
            first = type_rows[0]
            proposed_pattern = _CAPTURE_PATTERNS.get((pattern_class, semantic_type))
            subgroups.append({
                "semantic_type": semantic_type,
                "label": _SEMANTIC_LABEL.get(semantic_type, semantic_type),
                "token_count": len(type_rows),
                "file_count": sum(r["file_count"] for r in type_rows),
                "proposed_action": first["proposed_action"],
                "proposed_extract_as": first["proposed_extract_as"],
                "proposed_pattern": proposed_pattern,
                "proposed_value_type": _SEMANTIC_VALUE_TYPE.get(semantic_type, "text"),
                "bulk_disabled": semantic_type == "unclassified",
                "tokens": [dict(r) for r in type_rows],
            })

        all_groups.append({
            "pattern_class": pattern_class,
            "label": _CLASS_LABEL.get(pattern_class, pattern_class),
            "total_tokens": len(class_rows),
            "total_files": sum(r["file_count"] for r in class_rows),
            "subgroups": subgroups,
        })

    all_groups.sort(key=lambda g: g["total_files"], reverse=True)
    total = len(all_groups)
    if limit is not None:
        all_groups = all_groups[:limit]
    return all_groups, total


# ---------------------------------------------------------------------------
# Describe (Stage 3a)
# ---------------------------------------------------------------------------

def get_pending_describe_files(
    conn: sqlite3.Connection,
    *,
    scope: CorpusFilterSpec | None = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"""
        SELECT f.id, f.path, f.file_type, f.ext
        FROM files f
        LEFT JOIN descriptions d ON d.file_id = f.id
        WHERE f.canonical_id IS NULL
          AND (d.file_id IS NULL OR d.pass1_status IN ('pending', 'failed', 'skipped'))
          {frag}
        ORDER BY f.id
        """,
        params,
    ).fetchall()


def upsert_description(
    conn: sqlite3.Connection,
    file_id: int,
    description_raw: str | None,
    description_normalized: str | None,
    model: str,
    status: str,
) -> None:
    conn.execute(
        """
        INSERT INTO descriptions
            (file_id, description_raw, description_normalized, model, processed_at, pass1_status)
        VALUES (?, ?, ?, ?, datetime('now'), ?)
        ON CONFLICT(file_id) DO UPDATE SET
            description_raw        = excluded.description_raw,
            description_normalized = excluded.description_normalized,
            model                  = excluded.model,
            processed_at           = datetime('now'),
            pass1_status           = excluded.pass1_status
        """,
        (file_id, description_raw, description_normalized, model, status),
    )


def insert_video_frame(
    conn: sqlite3.Connection,
    file_id: int,
    frame_index: int,
    timestamp_ms: int,
    frame_phash: str | None,
    description: str | None,
    model: str,
) -> None:
    conn.execute(
        """
        INSERT INTO video_frames
            (file_id, frame_index, timestamp_ms, frame_phash, description, model, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (file_id, frame_index, timestamp_ms, frame_phash, description, model),
    )


def delete_video_frames_for_file(conn: sqlite3.Connection, file_id: int) -> None:
    conn.execute("DELETE FROM video_frames WHERE file_id = ?", (file_id,))


def reset_describe_to_pending(conn: sqlite3.Connection) -> int:
    cur = conn.execute("UPDATE descriptions SET pass1_status = 'pending'")
    conn.commit()
    return cur.rowcount


def get_describe_counts(
    conn: sqlite3.Connection,
    *,
    scope: "CorpusFilterSpec | None" = None,
) -> dict:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN d.pass1_status = 'done' THEN 1 ELSE 0 END) AS done,
            SUM(CASE WHEN d.pass1_status = 'failed' THEN 1 ELSE 0 END) AS failed,
            COUNT(*) AS total
        FROM files f
        LEFT JOIN descriptions d ON d.file_id = f.id
        WHERE f.canonical_id IS NULL
          {frag}
        """,
        params,
    ).fetchone()
    return {"done": row["done"] or 0, "failed": row["failed"] or 0, "total": row["total"] or 0}


# ---------------------------------------------------------------------------
# Transcribe (Stage 3b)
# ---------------------------------------------------------------------------

def get_pending_transcribe_files(
    conn: sqlite3.Connection,
    *,
    scope: CorpusFilterSpec | None = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"""
        SELECT f.id, f.path, f.file_type
        FROM files f
        LEFT JOIN transcriptions t ON t.file_id = f.id
        WHERE f.canonical_id IS NULL
          AND f.file_type IN ('audio', 'video')
          AND (t.file_id IS NULL OR t.transcribe_status IN ('failed', 'pending'))
          {frag}
        ORDER BY f.id
        """,
        params,
    ).fetchall()


def get_transcribe_counts(
    conn: sqlite3.Connection,
    *,
    scope: "CorpusFilterSpec | None" = None,
) -> dict:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN t.transcribe_status = 'done' THEN 1 ELSE 0 END) AS done,
            SUM(CASE WHEN t.transcribe_status = 'failed' THEN 1 ELSE 0 END) AS failed,
            COUNT(*) AS total
        FROM files f
        LEFT JOIN transcriptions t ON t.file_id = f.id
        WHERE f.canonical_id IS NULL
          AND f.file_type IN ('audio', 'video')
          {frag}
        """,
        params,
    ).fetchone()
    return {"done": row["done"] or 0, "failed": row["failed"] or 0, "total": row["total"] or 0}


def upsert_transcription(
    conn: sqlite3.Connection,
    file_id: int,
    transcript_text: str | None,
    language: str | None,
    duration_ms: int | None,
    model: str,
    status: str,
) -> None:
    conn.execute(
        """
        INSERT INTO transcriptions
            (file_id, transcript_text, language, duration_ms, model, processed_at, transcribe_status)
        VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
        ON CONFLICT(file_id) DO UPDATE SET
            transcript_text   = excluded.transcript_text,
            language          = excluded.language,
            duration_ms       = excluded.duration_ms,
            model             = excluded.model,
            processed_at      = datetime('now'),
            transcribe_status = excluded.transcribe_status
        """,
        (file_id, transcript_text, language, duration_ms, model, status),
    )


def upsert_transcript_segment(
    conn: sqlite3.Connection,
    file_id: int,
    start_ms: int,
    end_ms: int,
    text: str,
    avg_logprob: float | None,
) -> None:
    conn.execute(
        "INSERT INTO transcript_segments (file_id, start_ms, end_ms, text, avg_logprob)"
        " VALUES (?, ?, ?, ?, ?)",
        (file_id, start_ms, end_ms, text, avg_logprob),
    )


def delete_transcript_segments_for_file(conn: sqlite3.Connection, file_id: int) -> None:
    conn.execute("DELETE FROM transcript_segments WHERE file_id = ?", (file_id,))


def reset_transcribe_to_pending(
    conn: sqlite3.Connection,
    model_name: str | None = None,
) -> int:
    if model_name:
        cur = conn.execute(
            """
            UPDATE transcriptions SET transcribe_status = 'pending'
            WHERE model = ?
              AND file_id IN (SELECT id FROM files WHERE file_type IN ('audio', 'video'))
            """,
            (model_name,),
        )
    else:
        cur = conn.execute(
            """
            UPDATE transcriptions SET transcribe_status = 'pending'
            WHERE file_id IN (SELECT id FROM files WHERE file_type IN ('audio', 'video'))
            """
        )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Aesthetic (Stage KB.9)
# ---------------------------------------------------------------------------

def get_pending_aesthetic_files(
    conn: sqlite3.Connection,
    model_name: str,
    *,
    scope: CorpusFilterSpec | None = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"""
        SELECT f.id, f.path, f.file_type
        FROM files f
        LEFT JOIN file_aesthetic fa ON fa.file_id = f.id AND fa.model_name = ?
        WHERE f.file_type = 'images'
          AND f.canonical_id IS NULL
          AND fa.id IS NULL
          {frag}
        ORDER BY f.id
        """,
        [model_name] + params,
    ).fetchall()


def upsert_aesthetic_score(
    conn: sqlite3.Connection,
    file_id: int,
    model_name: str,
    score: float,
    band: str,
) -> None:
    conn.execute(
        """
        INSERT INTO file_aesthetic (file_id, model_name, score, band, scored_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(file_id, model_name) DO UPDATE SET
            score     = excluded.score,
            band      = excluded.band,
            scored_at = datetime('now')
        """,
        (file_id, model_name, score, band),
    )


def compute_combined_rank_scores(conn: sqlite3.Connection) -> int:
    """Rank-normalise each real model's scores to [0,1], average per file, upsert as combined_rank."""
    rows = conn.execute(
        """
        SELECT file_id, model_name, score
        FROM file_aesthetic
        WHERE model_name != 'combined_rank'
        ORDER BY model_name, score ASC
        """
    ).fetchall()

    if not rows:
        return 0

    from collections import defaultdict
    by_model: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for row in rows:
        by_model[row["model_name"]].append((row["file_id"], row["score"]))

    if len(by_model) < 2:
        return 0

    # rank-normalise each model: assign i/(N-1) where i is 0-based rank ascending
    rank_scores: dict[int, list[float]] = defaultdict(list)
    for scores_list in by_model.values():
        n = len(scores_list)
        for i, (file_id, _) in enumerate(scores_list):
            rank_norm = i / (n - 1) if n > 1 else 0.5
            rank_scores[file_id].append(rank_norm)

    # average across models; only include files scored by all models
    n_models = len(by_model)
    count = 0
    for file_id, model_rank_list in rank_scores.items():
        if len(model_rank_list) < n_models:
            continue
        avg = sum(model_rank_list) / len(model_rank_list)
        band = _combined_rank_band(avg)
        conn.execute(
            """
            INSERT INTO file_aesthetic (file_id, model_name, score, band, scored_at)
            VALUES (?, 'combined_rank', ?, ?, datetime('now'))
            ON CONFLICT(file_id, model_name) DO UPDATE SET
                score     = excluded.score,
                band      = excluded.band,
                scored_at = datetime('now')
            """,
            (file_id, avg, band),
        )
        count += 1

    conn.commit()
    return count


def _combined_rank_band(score: float) -> str:
    if score < 0.25:
        return "poor"
    if score < 0.5:
        return "average"
    if score < 0.75:
        return "good"
    return "excellent"


def reset_aesthetic_scores(conn: sqlite3.Connection, model_name: str | None = None) -> int:
    if model_name:
        cur = conn.execute("DELETE FROM file_aesthetic WHERE model_name = ?", (model_name,))
    else:
        cur = conn.execute("DELETE FROM file_aesthetic")
    conn.commit()
    return cur.rowcount


def get_aesthetic_scores_for_export(
    conn: sqlite3.Connection,
    model_name: str | None = None,
    min_score: float | None = None,
) -> list[dict]:
    """Return one row per file with nima_score, clip_score, combined_rank, band, scored_at.

    When model_name + min_score are given, only files where that model's score >= min_score
    are returned.
    """
    filter_model = model_name or "combined_rank"
    params: list = []

    having_clause = ""
    if min_score is not None:
        having_clause = "HAVING MAX(CASE WHEN fa.model_name = ? THEN fa.score END) >= ?"
        params.extend([filter_model, min_score])

    sql = f"""
        SELECT
            f.path AS file_path,
            MAX(CASE WHEN fa.model_name = 'nima_mobilenet'  THEN fa.score END) AS nima_score,
            MAX(CASE WHEN fa.model_name = 'clip_vit_b32'    THEN fa.score END) AS clip_score,
            MAX(CASE WHEN fa.model_name = 'combined_rank'   THEN fa.score END) AS combined_rank,
            MAX(CASE WHEN fa.model_name = 'combined_rank'   THEN fa.band  END) AS band,
            MAX(fa.scored_at) AS scored_at
        FROM file_aesthetic fa
        JOIN files f ON f.id = fa.file_id
        GROUP BY fa.file_id
        {having_clause}
        ORDER BY f.path
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Technical quality scores
# ---------------------------------------------------------------------------

def get_pending_quality_files(
    conn: sqlite3.Connection,
    *,
    scope: CorpusFilterSpec | None = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"""
        SELECT f.id, f.path, f.file_type
        FROM files f
        LEFT JOIN file_quality fq ON fq.file_id = f.id
        WHERE f.file_type IN ('images', 'video')
          AND f.canonical_id IS NULL
          AND fq.file_id IS NULL
          {frag}
        ORDER BY f.id
        """,
        params,
    ).fetchall()


def upsert_quality_score(
    conn: sqlite3.Connection,
    file_id: int,
    sharpness: float,
    exposure: float,
    highlights: float,
    shadows: float,
    frame_count: int,
    luminance_std_dev: float | None = None,
    saturation_mean: float | None = None,
    dominant_hue: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO file_quality
            (file_id, sharpness, exposure, highlights, shadows, frame_count,
             luminance_std_dev, saturation_mean, dominant_hue, scored_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(file_id) DO UPDATE SET
            sharpness         = excluded.sharpness,
            exposure          = excluded.exposure,
            highlights        = excluded.highlights,
            shadows           = excluded.shadows,
            frame_count       = excluded.frame_count,
            luminance_std_dev = excluded.luminance_std_dev,
            saturation_mean   = excluded.saturation_mean,
            dominant_hue      = excluded.dominant_hue,
            scored_at         = datetime('now')
        """,
        (file_id, sharpness, exposure, highlights, shadows, frame_count,
         luminance_std_dev, saturation_mean, dominant_hue),
    )


def compute_quality_rank_scores(conn: sqlite3.Connection) -> int:
    """Rank-normalise quality metrics and compute a combined quality_rank per file."""
    rows = conn.execute(
        "SELECT file_id, sharpness, exposure, highlights, shadows FROM file_quality"
    ).fetchall()
    if not rows:
        return 0

    import numpy as np

    file_ids = [r["file_id"] for r in rows]
    sharpness_vals = np.array([r["sharpness"] for r in rows], dtype=float)
    exposure_vals  = np.array([r["exposure"]  for r in rows], dtype=float)
    highlight_vals = np.array([r["highlights"] for r in rows], dtype=float)
    shadow_vals    = np.array([r["shadows"]    for r in rows], dtype=float)

    n = len(rows)

    def _rank_norm(vals: np.ndarray) -> np.ndarray:
        order = np.argsort(vals)
        ranks = np.empty(n, dtype=float)
        ranks[order] = np.arange(n) / max(n - 1, 1)
        return ranks

    sharpness_rank  = _rank_norm(sharpness_vals)
    exposure_score  = 1.0 - 2.0 * np.abs(exposure_vals - 0.5)
    highlight_score = 1.0 - highlight_vals
    shadow_score    = 1.0 - shadow_vals

    combined = (sharpness_rank + exposure_score + highlight_score + shadow_score) / 4.0

    for i, file_id in enumerate(file_ids):
        conn.execute(
            "UPDATE file_quality SET quality_rank = ? WHERE file_id = ?",
            (float(combined[i]), file_id),
        )

    conn.commit()
    return n


def reset_quality_scores(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM file_quality")
    conn.commit()
    return cur.rowcount


def get_quality_scores_for_export(
    conn: sqlite3.Connection,
    min_quality: float | None = None,
) -> list[dict]:
    where = "WHERE fq.quality_rank >= ?" if min_quality is not None else ""
    params = [min_quality] if min_quality is not None else []
    rows = conn.execute(
        f"""
        SELECT
            f.path AS file_path,
            fq.sharpness,
            fq.exposure,
            fq.highlights,
            fq.shadows,
            fq.quality_rank,
            fq.frame_count,
            fq.scored_at
        FROM file_quality fq
        JOIN files f ON f.id = fq.file_id
        {where}
        ORDER BY f.path
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Export readers
# ---------------------------------------------------------------------------

def get_export_descriptions(conn: sqlite3.Connection, scope_where: str = "") -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT f.path AS file_path,"
        " COALESCE(d.description_normalized, d.description_raw) AS description,"
        " d.model, d.processed_at"
        " FROM descriptions d JOIN files f ON f.id = d.file_id"
        f" WHERE 1=1 {scope_where}"
        " ORDER BY f.path"
    ).fetchall()


def get_export_tags(conn: sqlite3.Connection, scope_where: str = "") -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT f.path AS file_path,"
        " r.tags_json AS tags,"
        " r.refined_description,"
        " r.new_terms_proposed_json AS new_terms_proposed"
        " FROM retag_output r JOIN files f ON f.id = r.file_id"
        f" WHERE 1=1 {scope_where}"
        " ORDER BY f.path"
    ).fetchall()


# ---------------------------------------------------------------------------
# Sources — write-back
# ---------------------------------------------------------------------------

def update_source_ingested(conn: sqlite3.Connection, source_id: int, file_count: int) -> None:
    conn.execute(
        "UPDATE sources SET last_ingested_at = datetime('now'), file_count_ingested = ? WHERE id = ?",
        (file_count, source_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Corpus statistics
# ---------------------------------------------------------------------------

def get_corpus_stats(conn: sqlite3.Connection, kb_conn: sqlite3.Connection) -> dict:
    def _pct(covered: int, total: int) -> float:
        if total == 0:
            return 0.0
        return round(covered / total * 100, 1)

    total_files: int = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    by_type: dict[str, int] = {}
    for row in conn.execute("SELECT file_type, COUNT(*) as cnt FROM files GROUP BY file_type").fetchall():
        by_type[row["file_type"] or "other"] = row["cnt"]

    duplicates: int = conn.execute(
        "SELECT COUNT(*) FROM files WHERE canonical_id IS NOT NULL"
    ).fetchone()[0]
    sources_count: int = conn.execute(
        "SELECT COUNT(*) FROM sources WHERE removed_at IS NULL"
    ).fetchone()[0]

    checkpoints: dict[str, dict] = {}
    for row in conn.execute("SELECT * FROM pipeline_checkpoints").fetchall():
        checkpoints[row["stage"]] = dict(row)

    hash_covered: int = conn.execute(
        "SELECT COUNT(*) FROM files WHERE sha256 IS NOT NULL"
    ).fetchone()[0]

    desc_eligible: int = conn.execute(
        "SELECT COUNT(*) FROM files WHERE file_type IN ('images', 'video')"
    ).fetchone()[0]
    desc_covered: int = conn.execute(
        "SELECT COUNT(*) FROM descriptions WHERE pass1_status = 'done'"
    ).fetchone()[0]

    trans_eligible: int = conn.execute(
        "SELECT COUNT(*) FROM files WHERE file_type IN ('audio', 'video')"
    ).fetchone()[0]
    trans_covered: int = conn.execute(
        "SELECT COUNT(*) FROM transcriptions WHERE transcribe_status = 'done'"
    ).fetchone()[0]

    retag_covered: int = conn.execute(
        "SELECT COUNT(DISTINCT file_id) FROM retag_output"
    ).fetchone()[0]

    vocab_terms: int = kb_conn.execute("SELECT COUNT(*) FROM vocabulary").fetchone()[0]
    with_synonyms: int = kb_conn.execute(
        "SELECT COUNT(*) FROM vocabulary"
        " WHERE synonyms_json IS NOT NULL AND synonyms_json != '[]'"
    ).fetchone()[0]

    nima_scored: int = conn.execute(
        "SELECT COUNT(*) FROM file_aesthetic WHERE model_name = 'nima_mobilenet'"
    ).fetchone()[0]
    clip_scored: int = conn.execute(
        "SELECT COUNT(*) FROM file_aesthetic WHERE model_name = 'clip_vit_b32'"
    ).fetchone()[0]
    combined_rank: int = conn.execute(
        "SELECT COUNT(*) FROM file_aesthetic WHERE model_name = 'combined_rank'"
    ).fetchone()[0]

    cp_ingest = checkpoints.get("ingest", {})

    return {
        "files": {
            "total": total_files,
            "by_type": by_type,
            "duplicates": duplicates,
            "sources": sources_count,
        },
        "stages": {
            "ingest": {
                "files_processed": cp_ingest.get("files_processed") or 0,
                "last_run_at": cp_ingest.get("last_run_at"),
                "duration_seconds": cp_ingest.get("duration_seconds"),
            },
            "hash": {
                "covered": hash_covered,
                "eligible": total_files,
                "total": total_files,
                "eligible_pct": _pct(hash_covered, total_files),
                "total_pct": _pct(hash_covered, total_files),
                "last_run_at": checkpoints.get("hash", {}).get("last_run_at"),
            },
            "describe": {
                "covered": desc_covered,
                "eligible": desc_eligible,
                "total": total_files,
                "eligible_pct": _pct(desc_covered, desc_eligible),
                "total_pct": _pct(desc_covered, total_files),
                "last_run_at": checkpoints.get("describe", {}).get("last_run_at"),
            },
            "transcribe": {
                "covered": trans_covered,
                "eligible": trans_eligible,
                "total": total_files,
                "eligible_pct": _pct(trans_covered, trans_eligible),
                "total_pct": _pct(trans_covered, total_files),
                "last_run_at": checkpoints.get("transcribe", {}).get("last_run_at"),
            },
            "retag": {
                "covered": retag_covered,
                "eligible": total_files,
                "total": total_files,
                "eligible_pct": _pct(retag_covered, total_files),
                "total_pct": _pct(retag_covered, total_files),
                "last_run_at": checkpoints.get("retag", {}).get("last_run_at"),
            },
        },
        "vocabulary": {
            "terms": vocab_terms,
            "with_synonyms": with_synonyms,
        },
        "aesthetic": {
            "nima_scored": nima_scored,
            "clip_scored": clip_scored,
            "combined_rank": combined_rank,
        },
        "quality": {
            "scored": conn.execute("SELECT COUNT(*) FROM file_quality").fetchone()[0],
        },
    }


# ---------------------------------------------------------------------------
# Temporal fields
# ---------------------------------------------------------------------------

def upsert_temporal_fields(
    conn: sqlite3.Connection,
    file_id: int,
    fields: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO file_temporal_fields
            (file_id, year, decade, month_name, day_name, season, time_of_day, holiday)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET
            year        = excluded.year,
            decade      = excluded.decade,
            month_name  = excluded.month_name,
            day_name    = excluded.day_name,
            season      = excluded.season,
            time_of_day = excluded.time_of_day,
            holiday     = excluded.holiday,
            derived_at  = datetime('now')
        """,
        (
            file_id,
            fields.get("year"),
            fields.get("decade"),
            fields.get("month_name"),
            fields.get("day_name"),
            fields.get("season"),
            fields.get("time_of_day"),
            fields.get("holiday"),
        ),
    )


def reset_temporal_fields(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM file_temporal_fields")
    conn.commit()
    return cur.rowcount


def get_export_temporal_fields(conn: sqlite3.Connection, scope_where: str = "") -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT f.path, ft.year, ft.decade, ft.month_name, ft.day_name,
               ft.season, ft.time_of_day, ft.holiday
        FROM file_temporal_fields ft
        JOIN files f ON f.id = ft.file_id
        WHERE 1=1 {scope_where}
        ORDER BY f.path
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# Face regions
# ---------------------------------------------------------------------------

def get_files_without_face_regions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, path FROM files
        WHERE file_type = 'images'
          AND id NOT IN (SELECT DISTINCT file_id FROM file_face_regions WHERE source = 'ml')
        ORDER BY path
        """
    ).fetchall()


def get_files_without_meta_face_regions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, path FROM files
        WHERE file_type = 'images'
          AND id NOT IN (SELECT DISTINCT file_id FROM file_face_regions WHERE source = 'metadata')
        ORDER BY path
        """
    ).fetchall()


def upsert_face_region(
    conn: sqlite3.Connection,
    file_id: int,
    region_index: int,
    bbox_json: str | None,
    embedding_blob: bytes,
    person_id: int | None,
    similarity: float | None,
    source: str = "ml",
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO file_face_regions
            (file_id, region_index, source, bbox, embedding, person_id, similarity)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (file_id, region_index, source, bbox_json, embedding_blob, person_id, similarity),
    )


def get_face_regions_for_file(conn: sqlite3.Connection, file_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM file_face_regions WHERE file_id = ? ORDER BY region_index",
        (file_id,),
    ).fetchall()


def get_face_regions_for_export(conn: sqlite3.Connection) -> list[ClusterAssignment]:
    rows = conn.execute(
        """
        SELECT f.path AS file_path, ffr.region_index, ffr.person_id,
               ffr.similarity, ffr.bbox
        FROM file_face_regions ffr
        JOIN files f ON f.id = ffr.file_id
        ORDER BY f.path, ffr.region_index
        """
    ).fetchall()
    return [
        ClusterAssignment(
            file_path=row["file_path"],
            person_id=row["person_id"],
            score=row["similarity"],
            extra={"region_index": row["region_index"], "bbox": row["bbox"]},
        )
        for row in rows
    ]


def get_face_embeddings_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.path AS file_path, ffr.region_index, ffr.embedding
        FROM file_face_regions ffr
        JOIN files f ON f.id = ffr.file_id
        ORDER BY f.path, ffr.region_index
        """
    ).fetchall()


def reset_face_regions(conn: sqlite3.Connection, source: str | None = None) -> int:
    if source is not None:
        cur = conn.execute("DELETE FROM file_face_regions WHERE source = ?", (source,))
    else:
        cur = conn.execute("DELETE FROM file_face_regions")
    conn.commit()
    return cur.rowcount


def reset_meta_face_regions(conn: sqlite3.Connection) -> int:
    return reset_face_regions(conn, source="metadata")


def get_face_clusters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, centroid, member_count, spread FROM face_clusters ORDER BY id"
    ).fetchall()


def upsert_face_cluster(
    conn: sqlite3.Connection,
    cluster_id: int | None,
    centroid_blob: bytes,
    member_count: int,
    spread: float | None,
) -> int:
    if cluster_id is None:
        cur = conn.execute(
            "INSERT INTO face_clusters (centroid, member_count, spread) VALUES (?, ?, ?)",
            (centroid_blob, member_count, spread),
        )
        return cur.lastrowid
    conn.execute(
        "UPDATE face_clusters SET centroid = ?, member_count = ?, spread = ? WHERE id = ?",
        (centroid_blob, member_count, spread, cluster_id),
    )
    return cluster_id


def insert_face_cluster_member(
    conn: sqlite3.Connection,
    cluster_id: int,
    file_id: int,
    region_index: int,
    similarity: float | None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO face_cluster_members
            (cluster_id, file_id, region_index, similarity)
        VALUES (?, ?, ?, ?)
        """,
        (cluster_id, file_id, region_index, similarity),
    )


# ---------------------------------------------------------------------------
# Voice embeddings
# ---------------------------------------------------------------------------

def get_files_without_voice_embedding(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, path FROM files
        WHERE file_type IN ('audio', 'video')
          AND id NOT IN (SELECT file_id FROM file_voice_embeddings)
        ORDER BY path
        """
    ).fetchall()


def upsert_voice_embedding(
    conn: sqlite3.Connection,
    file_id: int,
    embedding_blob: bytes,
    model: str,
    duration_ms: int | None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO file_voice_embeddings
            (file_id, embedding, model, duration_ms)
        VALUES (?, ?, ?, ?)
        """,
        (file_id, embedding_blob, model, duration_ms),
    )


def get_voice_embeddings_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.path, fve.file_id AS person_id, fve.duration_ms, fve.model
        FROM file_voice_embeddings fve
        JOIN files f ON f.id = fve.file_id
        ORDER BY f.path
        """
    ).fetchall()


def reset_voice_embeddings(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM file_voice_embeddings")
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Voice segments (diarization)
# ---------------------------------------------------------------------------

def get_files_without_voice_segments(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, path FROM files
        WHERE file_type IN ('audio', 'video')
          AND id NOT IN (SELECT DISTINCT file_id FROM file_voice_segments)
        ORDER BY path
        """
    ).fetchall()


def upsert_voice_segment(
    conn: sqlite3.Connection,
    file_id: int,
    segment_index: int,
    start_ms: int,
    end_ms: int,
    speaker_label: str,
    embedding: bytes | None,
    cluster_id: int | None,
    person_id: int | None,
    similarity: float | None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO file_voice_segments
            (file_id, segment_index, start_ms, end_ms, speaker_label,
             embedding, cluster_id, person_id, similarity)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (file_id, segment_index, start_ms, end_ms, speaker_label,
         embedding, cluster_id, person_id, similarity),
    )


def get_voice_segments_for_file(conn: sqlite3.Connection, file_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM file_voice_segments WHERE file_id = ? ORDER BY segment_index",
        (file_id,),
    ).fetchall()


def get_voice_segments_for_export(conn: sqlite3.Connection) -> list[ClusterAssignment]:
    rows = conn.execute(
        """
        SELECT f.path, fvs.start_ms, fvs.end_ms, fvs.speaker_label,
               fvs.cluster_id, fvs.person_id, fvs.similarity
        FROM file_voice_segments fvs
        JOIN files f ON f.id = fvs.file_id
        ORDER BY f.path, fvs.segment_index
        """
    ).fetchall()
    return [
        ClusterAssignment(
            file_path=row["path"],
            person_id=row["person_id"],
            score=row["similarity"],
            cluster_id=row["cluster_id"],
            extra={
                "start_ms": row["start_ms"],
                "end_ms": row["end_ms"],
                "speaker_label": row["speaker_label"],
            },
        )
        for row in rows
    ]


def reset_voice_segments(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM file_voice_segments")
    conn.commit()
    return cur.rowcount


def get_voice_speaker_clusters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, centroid, member_count, spread, label, person_id FROM voice_speaker_clusters ORDER BY id"
    ).fetchall()


def upsert_voice_speaker_cluster(
    conn: sqlite3.Connection,
    cluster_id: int | None,
    centroid_blob: bytes,
    member_count: int,
    spread: float | None,
) -> int:
    if cluster_id is None:
        cur = conn.execute(
            "INSERT INTO voice_speaker_clusters (centroid, member_count, spread) VALUES (?, ?, ?)",
            (centroid_blob, member_count, spread),
        )
        return cur.lastrowid
    conn.execute(
        "UPDATE voice_speaker_clusters SET centroid = ?, member_count = ?, spread = ? WHERE id = ?",
        (centroid_blob, member_count, spread, cluster_id),
    )
    return cluster_id


def get_pending_speaker_clusters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Unassigned clusters, each with one sample segment (lowest id) and its file path."""
    return conn.execute(
        """
        SELECT
            c.id,
            c.member_count,
            c.spread,
            c.centroid,
            s.file_id  AS sample_file_id,
            f.path     AS sample_path,
            s.start_ms AS sample_start_ms,
            s.end_ms   AS sample_end_ms
        FROM voice_speaker_clusters c
        LEFT JOIN file_voice_segments s
          ON s.id = (
              SELECT MIN(id) FROM file_voice_segments
              WHERE cluster_id = c.id AND embedding IS NOT NULL
          )
        LEFT JOIN files f ON f.id = s.file_id
        WHERE c.person_id IS NULL
        ORDER BY c.id
        """
    ).fetchall()


def get_assigned_speaker_clusters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Clusters that have been assigned to a person."""
    return conn.execute(
        "SELECT id, person_id, label, member_count FROM voice_speaker_clusters WHERE person_id IS NOT NULL ORDER BY id"
    ).fetchall()


def assign_speaker_cluster(
    conn: sqlite3.Connection,
    cluster_id: int,
    person_id: int,
    label: str,
) -> None:
    conn.execute(
        "UPDATE voice_speaker_clusters SET person_id = ?, label = ? WHERE id = ?",
        (person_id, label, cluster_id),
    )
    conn.execute(
        "UPDATE file_voice_segments SET person_id = ? WHERE cluster_id = ?",
        (person_id, cluster_id),
    )


def unassign_speaker_cluster(conn: sqlite3.Connection, cluster_id: int) -> None:
    conn.execute(
        "UPDATE voice_speaker_clusters SET person_id = NULL, label = NULL WHERE id = ?",
        (cluster_id,),
    )
    conn.execute(
        "UPDATE file_voice_segments SET person_id = NULL WHERE cluster_id = ?",
        (cluster_id,),
    )


# ---------------------------------------------------------------------------
# Transcript speaker attribution (KB.P19)
# ---------------------------------------------------------------------------

def get_files_pending_speaker_attribution(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Files with at least one transcript_segment lacking speaker_label AND with voice segments."""
    return conn.execute(
        """
        SELECT DISTINCT f.id, f.path
        FROM files f
        WHERE EXISTS (
            SELECT 1 FROM transcript_segments
            WHERE file_id = f.id AND speaker_label IS NULL
        )
        AND EXISTS (
            SELECT 1 FROM file_voice_segments
            WHERE file_id = f.id
        )
        ORDER BY f.id
        """
    ).fetchall()


def set_transcript_segment_speaker(
    conn: sqlite3.Connection, segment_id: int, speaker_label: str
) -> None:
    conn.execute(
        "UPDATE transcript_segments SET speaker_label = ? WHERE id = ?",
        (speaker_label, segment_id),
    )


def reset_transcript_speaker_labels(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "UPDATE transcript_segments SET speaker_label = NULL WHERE speaker_label IS NOT NULL"
    )
    return cur.rowcount


def get_transcript_segments_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.path, ts.start_ms, ts.end_ms, ts.text, ts.speaker_label, ts.avg_logprob
        FROM transcript_segments ts
        JOIN files f ON ts.file_id = f.id
        ORDER BY f.path, ts.start_ms
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# Coverage analytics (KB.P20)
# ---------------------------------------------------------------------------

def get_coverage_per_file(conn: sqlite3.Connection, scope_where: str = "") -> list[sqlite3.Row]:
    """Per-file enrichment coverage flags via a single LEFT JOIN query."""
    return conn.execute(
        f"""
        SELECT
            f.path,
            CASE WHEN d.pass1_status = 'done'       THEN 1 ELSE 0 END AS has_description,
            CASE WHEN fdt.file_id  IS NOT NULL       THEN 1 ELSE 0 END AS has_tags,
            CASE WHEN fem.file_id  IS NOT NULL       THEN 1 ELSE 0 END AS has_entities,
            CASE WHEN gps.file_id  IS NOT NULL       THEN 1 ELSE 0 END AS has_gps,
            CASE WHEN fa.file_id   IS NOT NULL       THEN 1 ELSE 0 END AS has_aesthetic_score,
            CASE WHEN adate.file_id IS NOT NULL      THEN 1 ELSE 0 END AS has_asset_date,
            CASE WHEN fq.file_id   IS NOT NULL       THEN 1 ELSE 0 END AS has_quality_score,
            CASE WHEN tr.transcribe_status = 'done' THEN 1 ELSE 0 END AS has_transcript,
            CASE WHEN ffr.file_id  IS NOT NULL       THEN 1 ELSE 0 END AS has_face,
            CASE WHEN fve.file_id  IS NOT NULL       THEN 1 ELSE 0 END AS has_voice,
            COALESCE(tag_counts.n, 0)                                  AS tag_count,
            COALESCE(ent_counts.n, 0)                                  AS entity_count
        FROM files f
        LEFT JOIN descriptions d
            ON d.file_id = f.id
        LEFT JOIN (SELECT DISTINCT file_id FROM file_derived_tags) fdt
            ON fdt.file_id = f.id
        LEFT JOIN (SELECT DISTINCT file_id FROM file_entity_matches WHERE stale = 0) fem
            ON fem.file_id = f.id
        LEFT JOIN (SELECT DISTINCT file_id FROM file_metadata_fields WHERE canonical_name = 'gps_latitude') gps
            ON gps.file_id = f.id
        LEFT JOIN (SELECT DISTINCT file_id FROM file_aesthetic) fa
            ON fa.file_id = f.id
        LEFT JOIN (SELECT DISTINCT file_id FROM file_captured_fields WHERE field_name = 'asset_date') adate
            ON adate.file_id = f.id
        LEFT JOIN (SELECT DISTINCT file_id FROM file_quality) fq
            ON fq.file_id = f.id
        LEFT JOIN transcriptions tr
            ON tr.file_id = f.id
        LEFT JOIN (SELECT DISTINCT file_id FROM file_face_regions) ffr
            ON ffr.file_id = f.id
        LEFT JOIN (SELECT DISTINCT file_id FROM file_voice_embeddings) fve
            ON fve.file_id = f.id
        LEFT JOIN (SELECT file_id, COUNT(*) AS n FROM file_derived_tags GROUP BY file_id) tag_counts
            ON tag_counts.file_id = f.id
        LEFT JOIN (SELECT file_id, COUNT(*) AS n FROM file_entity_matches WHERE stale = 0 GROUP BY file_id) ent_counts
            ON ent_counts.file_id = f.id
        WHERE 1=1 {scope_where}
        ORDER BY f.path
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# Corpus file browser (KB.AK1)
# ---------------------------------------------------------------------------

_BROWSER_SORT_COLS = {
    "path": "f.path",
    "file_type": "f.file_type",
    "file_size": "f.file_size",
    "mtime": "f.mtime",
}

_BROWSER_STATE_CLAUSES = {
    "described": "d.pass1_status = 'done'",
    "not_described": "(d.pass1_status IS NULL OR d.pass1_status != 'done')",
    "transcribed": "t.transcribe_status = 'done'",
    "not_transcribed": "(t.transcribe_status IS NULL OR t.transcribe_status != 'done')",
    "hashed": "f.sha256 IS NOT NULL",
    "not_hashed": "f.sha256 IS NULL",
}


def _browser_where(spec: CorpusFilterSpec, state: str | None) -> tuple[str, list]:
    frag, params = spec.to_sql_fragment()
    clauses = ["f.canonical_id IS NULL"]
    state_clause = _BROWSER_STATE_CLAUSES.get(state) if state else None
    if state_clause:
        clauses.append(state_clause)
    return "WHERE " + " AND ".join(clauses) + frag, params


def get_files_for_browser(
    conn: sqlite3.Connection,
    spec: CorpusFilterSpec,
    *,
    state: str | None = None,
    sort_by: str = "path",
    sort_order: str = "asc",
    limit: int = 50,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """Paginated, filtered file listing for the Corpus Files browser."""
    where, params = _browser_where(spec, state)
    col = _BROWSER_SORT_COLS.get(sort_by, "f.path")
    direction = "DESC" if sort_order.lower() == "desc" else "ASC"
    return conn.execute(
        f"""
        SELECT
            f.id, f.path, f.filename, f.file_type, f.file_size, f.mtime, f.source_id,
            s.path AS source_path,
            CASE WHEN d.pass1_status = 'done'      THEN 1 ELSE 0 END AS has_description,
            CASE WHEN t.transcribe_status = 'done'  THEN 1 ELSE 0 END AS has_transcript,
            CASE WHEN f.sha256 IS NOT NULL           THEN 1 ELSE 0 END AS hashed,
            cap.value AS captured_date
        FROM files f
        JOIN sources s ON s.id = f.source_id
        LEFT JOIN descriptions d ON d.file_id = f.id
        LEFT JOIN transcriptions t ON t.file_id = f.id
        LEFT JOIN (
            SELECT file_id, MIN(value) AS value FROM file_metadata_fields
            WHERE canonical_name = 'captured_date'
            GROUP BY file_id
        ) cap ON cap.file_id = f.id
        {where}
        ORDER BY {col} {direction}
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()


def count_files_for_browser(
    conn: sqlite3.Connection,
    spec: CorpusFilterSpec,
    *,
    state: str | None = None,
) -> int:
    where, params = _browser_where(spec, state)
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM files f
        LEFT JOIN descriptions d ON d.file_id = f.id
        LEFT JOIN transcriptions t ON t.file_id = f.id
        {where}
        """,
        params,
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Geolabels
# ---------------------------------------------------------------------------

def upsert_geolabel(conn: sqlite3.Connection, file_id: int, label) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO file_geolabels
            (file_id, country, country_code, state, custom_region, method, confidence, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (file_id, label.country, label.country_code, label.state,
         label.custom_region, label.method, label.confidence),
    )


def get_geolocated_file_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT file_id FROM file_geolabels").fetchall()
    return {r[0] for r in rows}


def get_geolabels_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.path, gl.country, gl.country_code, gl.state, gl.custom_region,
               gl.method, gl.confidence, gl.resolved_at
        FROM file_geolabels gl
        JOIN files f ON f.id = gl.file_id
        ORDER BY f.path
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# Location labels (geo_meta stage)
# ---------------------------------------------------------------------------

def get_gps_files_without_location_label(
    conn: sqlite3.Connection,
    *,
    scope: CorpusFilterSpec | None = None,
) -> list[sqlite3.Row]:
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"""
        SELECT f.id, f.path,
               lat.value AS lat,
               lon.value AS lon
        FROM files f
        JOIN file_metadata_fields lat ON lat.file_id = f.id AND lat.canonical_name = 'exif_gps_lat'
        JOIN file_metadata_fields lon ON lon.file_id = f.id AND lon.canonical_name = 'exif_gps_lon'
        LEFT JOIN file_location_labels ll ON ll.file_id = f.id
        WHERE f.canonical_id IS NULL
          AND ll.file_id IS NULL
          {frag}
        ORDER BY f.id
        """,
        params,
    ).fetchall()


def upsert_location_label(
    conn: sqlite3.Connection,
    file_id: int,
    location: str | None,
    city: str | None,
    state: str | None,
    country: str | None,
    country_code: str | None,
    distance_m: float,
    matched_table: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO file_location_labels
            (file_id, location, city, state, country, country_code, distance_m, matched_table, matched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (file_id, location, city, state, country, country_code, distance_m, matched_table),
    )


def reset_location_labels(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM file_location_labels")
    conn.commit()
    return cur.rowcount


def get_location_labels_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.path, ll.location, ll.city, ll.state, ll.country, ll.country_code,
               ll.distance_m, ll.matched_table, ll.matched_at
        FROM file_location_labels ll
        JOIN files f ON f.id = ll.file_id
        ORDER BY f.path
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# GPS masking
# ---------------------------------------------------------------------------

def upsert_gps_mask(
    conn: sqlite3.Connection,
    file_id: int,
    zone_name: str,
    mode: str,
    masked_lat: float | None,
    masked_lon: float | None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO file_gps_masks
            (file_id, zone_name, mode, masked_lat, masked_lon, masked_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
        (file_id, zone_name, mode, masked_lat, masked_lon),
    )


def get_gps_masked_files(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT file_id FROM file_gps_masks").fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Validation runs
# ---------------------------------------------------------------------------

def insert_validation_run(
    conn: sqlite3.Connection,
    run_at: str,
    files_checked: int,
    ok_count: int,
    changed_count: int,
    moved_count: int,
    missing_count: int,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO validation_runs
            (run_at, files_checked, ok_count, changed_count, moved_count, missing_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_at, files_checked, ok_count, changed_count, moved_count, missing_count),
    )
    conn.commit()
    return cur.lastrowid


def insert_validation_result(
    conn: sqlite3.Connection,
    run_id: int,
    file_id: int,
    status: str,
    detail: str | None,
) -> None:
    conn.execute(
        "INSERT INTO validation_results (run_id, file_id, status, detail) VALUES (?, ?, ?, ?)",
        (run_id, file_id, status, detail),
    )


def get_latest_validation_summary(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM validation_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_gps_clusters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM gps_clusters ORDER BY id").fetchall()


def get_gps_cluster_assignments_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.path,
               a.cluster_id,
               gc.label AS cluster_label,
               gc.centroid_lat,
               gc.centroid_lon,
               a.distance_m
        FROM file_gps_cluster_assignments a
        JOIN files f ON f.id = a.file_id
        LEFT JOIN gps_clusters gc ON gc.id = a.cluster_id
        ORDER BY gc.label NULLS LAST, f.path
        """
    ).fetchall()


def clear_gps_clusters(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM file_gps_cluster_assignments")
    conn.execute("DELETE FROM gps_clusters")
    conn.commit()


def rename_gps_cluster(conn: sqlite3.Connection, cluster_id: int, label: str) -> None:
    conn.execute("UPDATE gps_clusters SET label=? WHERE id=?", (label, cluster_id))
    conn.commit()


def get_gps_cluster_with_assignments(conn: sqlite3.Connection, cluster_id: int) -> dict:
    row = conn.execute("SELECT * FROM gps_clusters WHERE id=?", (cluster_id,)).fetchone()
    if row is None:
        return {}
    paths = conn.execute(
        """
        SELECT f.path
        FROM file_gps_cluster_assignments a
        JOIN files f ON f.id = a.file_id
        WHERE a.cluster_id = ?
        ORDER BY f.path
        """,
        (cluster_id,),
    ).fetchall()
    result = dict(row)
    result["file_paths"] = [r["path"] for r in paths]
    return result


def get_analyse_token_by_id(conn: sqlite3.Connection, token_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT token FROM analyse_tokens WHERE id=?", (token_id,)).fetchone()


def get_candidate_by_id(conn: sqlite3.Connection, candidate_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT term FROM candidates WHERE id=?", (candidate_id,)).fetchone()


def get_file_path_by_id(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()


def get_validation_results_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.path, vr.status, vr.detail, vn.run_at AS checked_at
        FROM validation_results vr
        JOIN files f ON f.id = vr.file_id
        JOIN validation_runs vn ON vn.id = vr.run_id
        WHERE vr.run_id = (SELECT MAX(id) FROM validation_runs)
          AND vr.status != 'ok'
        ORDER BY f.path
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# Face cluster review (KB.Q3)
# ---------------------------------------------------------------------------

def get_pending_face_clusters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Face clusters not yet assigned to a person, with representative face info."""
    return conn.execute(
        """
        SELECT
            c.id,
            c.member_count,
            c.spread,
            c.centroid,
            m.id        AS rep_member_id,
            f.path      AS rep_file_path,
            fr.id       AS rep_face_region_id
        FROM face_clusters c
        LEFT JOIN face_cluster_members m
          ON m.id = (
              SELECT id FROM face_cluster_members
              WHERE cluster_id = c.id
              ORDER BY similarity DESC NULLS LAST
              LIMIT 1
          )
        LEFT JOIN files f ON f.id = m.file_id
        LEFT JOIN file_face_regions fr
          ON fr.file_id = m.file_id AND fr.region_index = m.region_index
        WHERE c.person_id IS NULL
        ORDER BY c.id
        """
    ).fetchall()


def get_assigned_face_clusters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Face clusters assigned to a person."""
    return conn.execute(
        "SELECT id, person_id, label, member_count, spread FROM face_clusters WHERE person_id IS NOT NULL ORDER BY id"
    ).fetchall()


def assign_face_cluster(
    conn: sqlite3.Connection,
    cluster_id: int,
    person_id: int,
    label: str,
) -> None:
    conn.execute(
        "UPDATE face_clusters SET person_id = ?, label = ? WHERE id = ?",
        (person_id, label, cluster_id),
    )
    conn.execute(
        """
        UPDATE file_face_regions SET person_id = ?
        WHERE (file_id, region_index) IN (
            SELECT file_id, region_index FROM face_cluster_members WHERE cluster_id = ?
        )
        """,
        (person_id, cluster_id),
    )


def unassign_face_cluster(conn: sqlite3.Connection, cluster_id: int) -> None:
    conn.execute(
        "UPDATE face_clusters SET person_id = NULL, label = NULL WHERE id = ?",
        (cluster_id,),
    )
    conn.execute(
        """
        UPDATE file_face_regions SET person_id = NULL
        WHERE (file_id, region_index) IN (
            SELECT file_id, region_index FROM face_cluster_members WHERE cluster_id = ?
        )
        """,
        (cluster_id,),
    )


def get_face_region_for_thumbnail(
    conn: sqlite3.Connection, face_region_id: int
) -> sqlite3.Row | None:
    """Return file path and bbox JSON for a single face region."""
    return conn.execute(
        """
        SELECT fr.id, fr.bbox, f.path AS file_path
        FROM file_face_regions fr
        JOIN files f ON f.id = fr.file_id
        WHERE fr.id = ?
        """,
        (face_region_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Summarize (Stage 3c)
# ---------------------------------------------------------------------------

def reset_summarize_to_pending(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE file_summaries SET status='pending' WHERE status='done'")
    conn.commit()


def get_pending_summarize_files(
    conn: sqlite3.Connection,
    *,
    scope: CorpusFilterSpec | None = None,
) -> list[sqlite3.Row]:
    """Files eligible for summarize: have a done description or transcription,
    no done summary, canonical files only."""
    spec = scope or CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    return conn.execute(
        f"""
        SELECT f.id
        FROM files f
        WHERE f.canonical_id IS NULL
          AND (
              EXISTS (SELECT 1 FROM descriptions d
                      WHERE d.file_id = f.id AND d.pass1_status = 'done')
           OR EXISTS (SELECT 1 FROM transcriptions t
                      WHERE t.file_id = f.id AND t.transcribe_status = 'done')
          )
          AND NOT EXISTS (
              SELECT 1 FROM file_summaries s
              WHERE s.file_id = f.id AND s.status = 'done'
          )
          {frag}
        ORDER BY f.id
        """,
        params,
    ).fetchall()


def upsert_file_summary(
    conn: sqlite3.Connection,
    file_id: int,
    summary_text: str | None,
    model: str,
    prompt_version: str,
    status: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO file_summaries
            (file_id, summary_text, model, prompt_version, processed_at, status)
        VALUES (?, ?, ?, ?, datetime('now'), ?)
        """,
        (file_id, summary_text, model, prompt_version, status),
    )


def get_file_summary(
    conn: sqlite3.Connection,
    file_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM file_summaries WHERE file_id = ?",
        (file_id,),
    ).fetchone()


def get_export_summaries(conn: sqlite3.Connection, scope_where: str = "") -> list[sqlite3.Row]:
    """Return done summaries joined to file path."""
    return conn.execute(
        f"""
        SELECT f.path AS file_path, s.summary_text, s.model, s.processed_at
        FROM file_summaries s
        JOIN files f ON f.id = s.file_id
        WHERE s.status = 'done' {scope_where}
        ORDER BY f.path
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# VAD / has_speech
# ---------------------------------------------------------------------------

def get_has_speech(conn: sqlite3.Connection, file_id: int) -> bool | None:
    row = conn.execute("SELECT has_speech FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None or row["has_speech"] is None:
        return None
    return bool(row["has_speech"])


def set_has_speech(conn: sqlite3.Connection, file_id: int, value: bool) -> None:
    conn.execute("UPDATE files SET has_speech = ? WHERE id = ?", (int(value), file_id))


# ---------------------------------------------------------------------------
# Per-file context queries (used by src/text/context.py)
# ---------------------------------------------------------------------------

def get_file_filename(conn: sqlite3.Connection, file_id: int) -> str:
    row = conn.execute("SELECT filename FROM files WHERE id=?", (file_id,)).fetchone()
    return row["filename"] if row else ""


def get_description_for_file(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT description_normalized, description_raw FROM descriptions"
        " WHERE file_id=? AND pass1_status='done'",
        (file_id,),
    ).fetchone()


def get_file_transcript_segments(conn: sqlite3.Connection, file_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT start_ms, speaker_label, text FROM transcript_segments"
        " WHERE file_id=? ORDER BY start_ms",
        (file_id,),
    ).fetchall()


def get_file_transcription(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT transcript_text FROM transcriptions"
        " WHERE file_id=? AND transcribe_status='done'",
        (file_id,),
    ).fetchone()


def get_file_derived_tags(conn: sqlite3.Connection, file_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT tag FROM file_derived_tags WHERE file_id=?", (file_id,)
    ).fetchall()
    return [r["tag"] for r in rows]


def get_file_captured_fields(conn: sqlite3.Connection, file_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT field_name, value FROM file_captured_fields"
        " WHERE file_id=? AND value IS NOT NULL",
        (file_id,),
    ).fetchall()


def get_file_metadata_date(conn: sqlite3.Connection, file_id: int) -> str | None:
    row = conn.execute(
        "SELECT value FROM file_metadata_fields"
        " WHERE file_id=? AND canonical_name='captured_date' LIMIT 1",
        (file_id,),
    ).fetchone()
    return row["value"] if row else None


def get_file_geolabel(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT custom_region, state, country FROM file_geolabels WHERE file_id=? LIMIT 1",
        (file_id,),
    ).fetchone()
