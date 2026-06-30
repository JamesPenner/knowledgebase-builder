"""Stage 1.7 — Entity Match.

GPS sub-pass: haversine match against registered GPS entity tables.
Text sub-pass: trigger-word + key-column scan for text entity tables + people names.
"""
import json
import re
import threading
import time
from pathlib import Path

from src.config import Config
from src.db.corpus import (
    get_enrichment_text_for_file,
    get_files_for_text_match,
    get_files_with_gps,
    open_corpus,
    update_pipeline_checkpoint,
    upsert_entity_match,
)
from src.db.kb import (
    get_entity_links_by_parent,
    get_entity_table_rows,
    get_entity_tables,
    get_people_names,
    open_kb,
)
from src.pipeline.progress import ProgressReporter
from src.stages.classify_rules import _haversine_m

_LINK_MAX_DEPTH = 3


def _resolve_links(
    row_dict: dict,
    table_name: str,
    links_by_parent: dict[str, list[dict]],
    entity_row_lookup: dict[str, dict[str, dict]],
    visited: frozenset,
    max_depth: int = _LINK_MAX_DEPTH,
) -> dict:
    if max_depth == 0 or not links_by_parent.get(table_name):
        return {}
    resolved: dict = {}
    for link in links_by_parent[table_name]:
        linked_table = link["linked_table"]
        if linked_table in visited:
            continue
        parent_val = str(row_dict.get(link["parent_column"]) or "").lower()
        if not parent_val:
            continue
        linked_row = entity_row_lookup.get(linked_table, {}).get(parent_val)
        if linked_row is None:
            continue
        nested = _resolve_links(
            linked_row, linked_table,
            links_by_parent, entity_row_lookup,
            visited | {table_name}, max_depth - 1,
        )
        entry = dict(linked_row)
        if nested:
            entry["_links"] = nested
        resolved[link.get("label") or linked_table] = entry
    return resolved


def run_entity_match(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    *,
    scope=None,
) -> None:
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    entity_tables = get_entity_tables(kb_conn)
    links_by_parent = get_entity_links_by_parent(kb_conn)

    entity_row_lookup: dict[str, dict[str, dict]] = {}
    for _tbl in entity_tables:
        _key_col = _tbl["key_column"] or "name"
        _rows = get_entity_table_rows(kb_conn, _tbl["table_name"])
        entity_row_lookup[_tbl["table_name"]] = {
            str(r[_key_col]).lower(): dict(r)
            for r in _rows
            if r[_key_col] is not None
        }

    people_names = get_people_names(kb_conn)
    kb_conn.close()

    gps_tables = [t for t in entity_tables if t["match_type"] == "gps"]
    text_tables = [t for t in entity_tables if t["match_type"] == "text"]

    start = time.monotonic()
    total_files = 0
    batch_size = 200

    # -----------------------------------------------------------------------
    # GPS sub-pass
    # -----------------------------------------------------------------------
    if gps_tables:
        gps_files = get_files_with_gps(corpus_conn)
        total_files += len(gps_files)

        for tbl in gps_tables:
            table_name = tbl["table_name"]
            key_col = tbl["key_column"] or "location"
            lat_col = "latitude"
            lon_col = "longitude"
            thr_col = "threshold_m"

            entity_rows = list(entity_row_lookup.get(table_name, {}).values())

            for i, file_row in enumerate(gps_files):
                if cancel_event.is_set():
                    corpus_conn.commit()
                    corpus_conn.close()
                    progress.done()
                    return

                file_lat = file_row["lat"]
                file_lon = file_row["lon"]
                if file_lat is None or file_lon is None:
                    continue

                for entity_row in entity_rows:
                    try:
                        e_lat = float(entity_row[lat_col])
                        e_lon = float(entity_row[lon_col])
                        threshold = float(entity_row[thr_col] or 500)
                    except (TypeError, ValueError, IndexError):
                        continue

                    dist = _haversine_m(file_lat, file_lon, e_lat, e_lon)
                    if dist <= threshold:
                        loc_name = str(entity_row[key_col] or "")
                        row_dict = dict(entity_row)
                        _links = _resolve_links(
                            row_dict, table_name, links_by_parent,
                            entity_row_lookup, frozenset({table_name}),
                        )
                        if _links:
                            row_dict["_links"] = _links
                        payload = json.dumps(row_dict, default=str)
                        upsert_entity_match(
                            corpus_conn,
                            file_row["id"],
                            table_name,
                            loc_name,
                            "gps",
                            payload,
                        )

                if (i + 1) % batch_size == 0:
                    corpus_conn.commit()
                progress.update(i + 1, total_files)

        corpus_conn.commit()

    # -----------------------------------------------------------------------
    # Text sub-pass
    # -----------------------------------------------------------------------
    # Build name → person_id lookup for people matching
    name_to_person: dict[str, int] = {}
    for pn in people_names:
        name_to_person[pn["name"].lower()] = pn["person_id"]

    # Build trigger regex and key-value list for each text entity table
    text_table_data: list[dict] = []
    for tbl in text_tables:
        table_name = tbl["table_name"]
        trigger = tbl["trigger_word"] or ""
        key_col = tbl["key_column"] or "name"
        aliases_raw = tbl["trigger_aliases"] or "[]"
        try:
            aliases = json.loads(aliases_raw)
        except (json.JSONDecodeError, TypeError):
            aliases = []

        trigger_words = [trigger] + aliases
        trigger_pattern = "|".join(
            re.escape(w) for w in trigger_words if w
        )
        trigger_re = re.compile(r"\b(" + trigger_pattern + r")\b", re.IGNORECASE) if trigger_pattern else None

        entity_rows = list(entity_row_lookup.get(table_name, {}).values())

        key_values: list[tuple[str, str]] = []
        for entity_row in entity_rows:
            key_val = str(entity_row[key_col] or "").strip()
            if key_val and key_val != "-":
                key_values.append((key_val.lower(), key_val))

        text_table_data.append({
            "table_name": table_name,
            "trigger_re": trigger_re,
            "key_col": key_col,
            "key_values": key_values,
            "rows_by_key": entity_row_lookup.get(table_name, {}),
        })

    text_files = get_files_for_text_match(corpus_conn, scope=scope)
    total_files += len(text_files)

    for i, file_row in enumerate(text_files):
        if cancel_event.is_set():
            break

        text_blob = get_enrichment_text_for_file(corpus_conn, file_row["id"])
        text_lower = text_blob.lower()

        # Text entity tables
        for tbl_data in text_table_data:
            trigger_re = tbl_data["trigger_re"]
            if trigger_re and not trigger_re.search(text_lower):
                continue
            for key_lower, key_orig in tbl_data["key_values"]:
                if re.search(r"\b" + re.escape(key_lower) + r"\b", text_lower):
                    row_dict = dict(tbl_data["rows_by_key"].get(key_lower, {"matched_key": key_orig}))
                    _links = _resolve_links(
                        row_dict, tbl_data["table_name"], links_by_parent,
                        entity_row_lookup, frozenset({tbl_data["table_name"]}),
                    )
                    if _links:
                        row_dict["_links"] = _links
                    upsert_entity_match(
                        corpus_conn,
                        file_row["id"],
                        tbl_data["table_name"],
                        key_orig,
                        "text",
                        json.dumps(row_dict, default=str),
                    )

        # People name matching
        for name_lower, person_id in name_to_person.items():
            if re.search(r"\b" + re.escape(name_lower) + r"\b", text_lower):
                upsert_entity_match(
                    corpus_conn,
                    file_row["id"],
                    "people",
                    name_lower,
                    "text",
                    json.dumps({"person_id": person_id}),
                )

        if (i + 1) % batch_size == 0:
            corpus_conn.commit()
        progress.update(i + 1, total_files)

    corpus_conn.commit()

    duration = time.monotonic() - start
    update_pipeline_checkpoint(
        corpus_conn,
        stage="entity_match",
        files_processed=total_files,
        duration_seconds=duration,
    )
    corpus_conn.close()
    progress.done()
