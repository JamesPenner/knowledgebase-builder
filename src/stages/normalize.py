import re
import threading
import time
from pathlib import Path

from src.config import Config
from src.db.corpus import (
    get_files_for_normalize,
    open_corpus,
    update_filename_normalized,
    update_pipeline_checkpoint,
    upsert_captured_field,
)
from src.db.kb import (
    get_capture_rules,
    get_corrections_map,
    get_reject_tokens,
    get_stoplist_terms,
    get_substitute_rules,
    open_kb,
)
from src.pipeline.progress import ProgressReporter
from src.stages.analyse import tokenize_path


def apply_format_str(format_str: str | None, match: re.Match) -> str:
    """Render a capture rule format_str against a regex match.

    Syntax:
      {N}        → group N verbatim (1-indexed)
      {N:s:e}    → slice [s:e] of group N
    Falls back to group 1 verbatim when format_str is empty or None.
    """
    if not format_str:
        try:
            return match.group(1)
        except IndexError:
            return match.group(0)

    _TOKEN_RE = re.compile(r"\{(\d+)(?::(\d+):(\d+))?\}")

    def _replace(m: re.Match) -> str:
        group_n = int(m.group(1))
        text = match.group(group_n)
        if m.group(2) is not None:
            start, end = int(m.group(2)), int(m.group(3))
            return text[start:end]
        return text

    return _TOKEN_RE.sub(_replace, format_str)


def _matches_reject(token: str, reject_rules: list[dict]) -> bool:
    for rule in reject_rules:
        if rule["is_regex"]:
            if re.search(rule["pattern"], token):
                return True
        else:
            if token == rule["pattern"]:
                return True
    return False


def normalize_filename(
    filename: str,
    capture_rules: list[dict],
    reject_rules: list[dict],
    substitute_rules: list[dict],
    corrections: dict[str, str],
    stoplist: set[str],
) -> tuple[str, dict[str, str]]:
    """Return (normalized_name, captured_fields) for a single filename.

    captured_fields maps extract_as → formatted value for each matched capture rule.
    """
    stem = Path(filename).stem
    tokens = tokenize_path(stem)
    captured: dict[str, str] = {}
    kept_tokens: list[str] = []

    for token in tokens:
        lower = token.lower()

        if _matches_reject(lower, reject_rules):
            continue

        captured_this = False
        keep_after_capture = True
        for rule in capture_rules:
            try:
                m = re.match(rule["pattern"], lower)
            except re.error:
                continue
            if m:
                value = apply_format_str(rule.get("format_str"), m)
                captured[rule["extract_as"]] = value
                if rule.get("value_type") == "date" and rule.get("date_precision"):
                    captured[rule["extract_as"] + "_precision"] = rule["date_precision"]
                captured_this = True
                if not rule.get("keep_token", True):
                    keep_after_capture = False
                break  # first matching rule wins per token

        canonical = corrections.get(lower, lower)

        if canonical in stoplist:
            continue

        if not captured_this or keep_after_capture:
            kept_tokens.append(canonical)

    name = " ".join(kept_tokens)

    for rule in substitute_rules:
        if rule["applies_to"] in ("filename", "both"):
            try:
                name = re.sub(rule["pattern"], rule["replacement"], name)
            except re.error:
                pass

    return name.strip(), captured


def run_normalize(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    capture_rules = get_capture_rules(kb_conn)
    reject_rules = get_reject_tokens(kb_conn)
    substitute_rules = get_substitute_rules(kb_conn)
    corrections = get_corrections_map(kb_conn)
    stoplist = get_stoplist_terms(kb_conn, scope="global")
    kb_conn.close()

    files = get_files_for_normalize(corpus_conn)
    total = len(files)

    if not files:
        progress.done()
        corpus_conn.close()
        return

    start = time.monotonic()
    batch_size = 200

    for i, file_row in enumerate(files):
        if cancel_event.is_set():
            break

        normalized, captured = normalize_filename(
            file_row["filename"],
            capture_rules,
            reject_rules,
            substitute_rules,
            corrections,
            stoplist,
        )

        update_filename_normalized(corpus_conn, file_row["id"], normalized)
        for field_name, value in captured.items():
            upsert_captured_field(corpus_conn, file_row["id"], field_name, value)

        if (i + 1) % batch_size == 0:
            corpus_conn.commit()

        progress.update(i + 1, total)

    corpus_conn.commit()

    kw_rows = corpus_conn.execute(
        "SELECT file_id, canonical_name, keyword FROM file_metadata_keywords"
    ).fetchall()
    for kw_row in kw_rows:
        lower = kw_row["keyword"].lower()
        if lower in stoplist:
            normalized = None
        else:
            normalized = corrections.get(lower, kw_row["keyword"])
        corpus_conn.execute(
            """
            UPDATE file_metadata_keywords SET normalized_keyword = ?
            WHERE file_id = ? AND canonical_name = ? AND keyword = ?
            """,
            (normalized, kw_row["file_id"], kw_row["canonical_name"], kw_row["keyword"]),
        )
    if kw_rows:
        corpus_conn.commit()

    duration = time.monotonic() - start
    update_pipeline_checkpoint(
        corpus_conn,
        stage="normalize",
        files_processed=total,
        duration_seconds=duration,
    )
    corpus_conn.close()
    progress.done()
