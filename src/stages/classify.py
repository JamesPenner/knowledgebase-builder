"""Stage 1.8 — Classify.

Applies deterministic classify_rules to each file's fields and writes
file_derived_tags. Also fires life-event rules for person-matched files.
"""
import json
import threading
import time
from pathlib import Path

from src.config import Config
from src.db.corpus import (
    get_entity_matches_for_file,
    get_fields_for_classify,
    get_files_for_classify,
    open_corpus,
    update_pipeline_checkpoint,
    upsert_derived_tag,
)
from src.db.kb import get_classify_rules, open_kb
from src.pipeline.progress import ProgressReporter
from src.stages.classify_rules import evaluate_rule


def _life_event_tags(
    file_date: str,
    person_id: int,
    preferred_name: str,
    life_events: list,
) -> list[tuple[str, str]]:
    """Return [(tag, category)] derived from life events for one person."""
    results: list[tuple[str, str]] = []
    if not file_date or len(file_date) < 7:
        return results

    try:
        f_month = int(file_date[5:7])
        f_day = int(file_date[8:10]) if len(file_date) >= 10 else None
        f_year = int(file_date[:4])
    except (ValueError, IndexError):
        return results

    for ev in life_events:
        if ev["person_id"] != person_id:
            continue
        event_date = ev["event_date"] or ""
        if not event_date or len(event_date) < 5:
            continue

        try:
            e_month = int(event_date[5:7])
        except (ValueError, IndexError):
            continue

        if e_month != f_month:
            continue

        event_type = ev["event_type"]

        if event_type == "birth":
            if len(event_date) >= 10:
                e_day = int(event_date[8:10])
                if f_day is None or f_day != e_day:
                    continue
            try:
                e_year = int(event_date[:4])
                if f_year == e_year:
                    results.append((f"{preferred_name}'s Birthday", "life_event"))
                else:
                    age = f_year - e_year
                    results.append((f"{preferred_name}'s {age}th Birthday", "life_event"))
            except ValueError:
                results.append((f"{preferred_name}'s Birthday", "life_event"))

        elif event_type == "marriage":
            if len(event_date) < 10 or f_day is None:
                continue
            e_day = int(event_date[8:10])
            if f_day != e_day:
                continue
            try:
                e_year = int(event_date[:4])
                if f_year == e_year:
                    tag = f"{preferred_name} — Wedding Day"
                else:
                    years = f_year - e_year
                    tag = f"{preferred_name} — {years}th Anniversary"
                results.append((tag, "life_event"))
            except ValueError:
                results.append((f"{preferred_name} — Wedding Day", "life_event"))

        elif event_type == "death":
            if len(event_date) >= 10:
                e_day = int(event_date[8:10])
                if f_day is None or f_day != e_day:
                    continue
            results.append((f"In memory of {preferred_name}", "life_event"))

    return results


def run_classify(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    rules = [dict(r) for r in get_classify_rules(kb_conn, enabled_only=True)]

    # Pre-load all people data for life-event rules
    all_life_events = kb_conn.execute(
        "SELECT le.*, p.preferred_name FROM life_events le"
        " JOIN people p ON p.id = le.person_id"
    ).fetchall()
    kb_conn.close()

    files = get_files_for_classify(corpus_conn)
    total = len(files)
    start = time.monotonic()
    batch_size = 200

    for i, file_row in enumerate(files):
        if cancel_event.is_set():
            break

        fields = get_fields_for_classify(corpus_conn, file_row["id"])

        # Ensure calendar rules always have a best-available date under 'file_date'
        if "file_date" not in fields and "exif_date_taken" in fields:
            fields["file_date"] = fields["exif_date_taken"]

        # Apply all classify rules
        for rule in rules:
            tag = evaluate_rule(rule, fields)
            if tag:
                upsert_derived_tag(
                    corpus_conn,
                    file_row["id"],
                    tag,
                    rule["category"],
                    "classify_rule",
                    rule["id"],
                )

        # Life-event rules: check entity matches for known people
        if all_life_events:
            entity_matches = get_entity_matches_for_file(corpus_conn, file_row["id"])
            people_matches = [m for m in entity_matches if m["table_name"] == "people"]
            if people_matches:
                file_date = fields.get("file_date", "")
                seen_pids: set[int] = set()
                for match in people_matches:
                    try:
                        payload = json.loads(match["payload_json"] or "{}")
                        person_id = int(payload.get("person_id", 0))
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
                    if not person_id or person_id in seen_pids:
                        continue
                    seen_pids.add(person_id)

                    preferred = next(
                        (ev["preferred_name"] for ev in all_life_events
                         if ev["person_id"] == person_id),
                        None,
                    )
                    if preferred is None:
                        continue

                    for tag, category in _life_event_tags(
                        file_date, person_id, preferred, all_life_events
                    ):
                        upsert_derived_tag(
                            corpus_conn, file_row["id"], tag, category, "classify_rule"
                        )

        if (i + 1) % batch_size == 0:
            corpus_conn.commit()
        progress.update(i + 1, total)

    corpus_conn.commit()

    duration = time.monotonic() - start
    update_pipeline_checkpoint(
        corpus_conn,
        stage="classify",
        files_processed=total,
        duration_seconds=duration,
    )
    corpus_conn.close()
    progress.done()
