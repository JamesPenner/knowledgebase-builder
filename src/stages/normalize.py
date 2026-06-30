import re
import threading
import time
from pathlib import Path

from src.config import Config
from src.db.corpus import (
    get_all_pending_tokens,
    get_files_for_normalize,
    mark_analyse_tokens_decided,
    open_corpus,
    update_filename_normalized,
    update_pipeline_checkpoint,
    upsert_captured_field,
)
from src.db.kb import (
    get_pattern_rules,
    get_stoplist_terms,
    get_substitute_rules,
    get_token_rejections,
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


def normalize_filename(
    filename: str,
    pattern_rules: list[dict],
    substitute_rules: list[dict],
    stoplist: set[str],
    rejected_tokens: set[str] = frozenset(),
) -> tuple[str, dict[str, str]]:
    """Return (normalized_name, captured_fields) for a single filename.

    captured_fields maps extract_as → formatted value for each matched capture rule.
    First matching pattern_rule per token wins. Actions: reject, ignore, replace, capture.
    rejected_tokens is the set of exact tokens from token_rejections (review decisions).
    """
    stem = Path(filename).stem
    tokens = tokenize_path(stem)
    captured: dict[str, str] = {}
    kept_tokens: list[str] = []

    for token in tokens:
        lower = token.lower()
        if lower in rejected_tokens:
            continue
        token_rejected = False
        captured_this = False
        keep_after_capture = True

        for rule in pattern_rules:
            try:
                if rule["is_regex"]:
                    m = re.match(rule["pattern"], lower)
                else:
                    m = lower == rule["pattern"]
            except re.error:
                continue
            if not m:
                continue

            action = rule["action"]
            if action == "reject":
                token_rejected = True
            elif action == "ignore":
                captured_this = True
                keep_after_capture = False
            elif action == "replace":
                if rule["is_regex"] and isinstance(m, re.Match):
                    lower = apply_format_str(rule.get("replace_with"), m)
                else:
                    lower = rule.get("replace_with") or lower
            elif action == "capture":
                if rule["is_regex"] and isinstance(m, re.Match):
                    value = apply_format_str(
                        rule.get("format_str") or rule.get("replace_with"), m
                    )
                else:
                    value = rule.get("replace_with") or lower
                captured[rule["extract_as"]] = value
                if rule.get("value_type") == "date" and rule.get("date_precision"):
                    captured[rule["extract_as"] + "_precision"] = rule["date_precision"]
                captured_this = True
                if not rule.get("keep_token", True):
                    keep_after_capture = False
            break  # first matching rule wins per token

        if token_rejected:
            continue
        if lower in stoplist:
            continue
        if not captured_this or keep_after_capture:
            kept_tokens.append(lower)

    name = " ".join(kept_tokens)

    for rule in substitute_rules:
        if rule["applies_to"] in ("filename", "both"):
            try:
                name = re.sub(rule["pattern"], rule["replacement"], name)
            except re.error:
                pass

    return name.strip(), captured


def _auto_resolve_tokens(
    corpus_conn: object,
    pattern_rules: list[dict],
    rejected_tokens: set[str] = frozenset(),
) -> None:
    """Mark any pending analyse_tokens as decided if they match a pattern rule or rejection.

    A rule match means the user has an explicit opinion about that token class —
    no manual review needed regardless of the action (reject/ignore/replace/capture).
    """
    pending = get_all_pending_tokens(corpus_conn)
    if not pending:
        return

    resolved_ids = []
    for row in pending:
        token = row["token"].lower()
        if token in rejected_tokens:
            resolved_ids.append(row["id"])
            continue
        for rule in pattern_rules:
            try:
                matched = (
                    re.match(rule["pattern"], token)
                    if rule["is_regex"]
                    else token == rule["pattern"]
                )
            except re.error:
                continue
            if matched:
                resolved_ids.append(row["id"])
                break

    mark_analyse_tokens_decided(corpus_conn, resolved_ids)


def run_normalize(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    pattern_rules = get_pattern_rules(kb_conn)
    substitute_rules = get_substitute_rules(kb_conn)
    stoplist = get_stoplist_terms(kb_conn, scope="global")
    rejected_tokens = {r["token"].lower() for r in get_token_rejections(kb_conn)}
    # Build a replace map from exact replace rules for keyword normalization
    replace_map = {
        r["pattern"]: r["replace_with"]
        for r in pattern_rules
        if r["action"] == "replace" and not r["is_regex"] and r.get("replace_with")
    }
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
            pattern_rules,
            substitute_rules,
            stoplist,
            rejected_tokens,
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
            normalized = replace_map.get(lower, kw_row["keyword"])
        corpus_conn.execute(
            """
            UPDATE file_metadata_keywords SET normalized_keyword = ?
            WHERE file_id = ? AND canonical_name = ? AND keyword = ?
            """,
            (normalized, kw_row["file_id"], kw_row["canonical_name"], kw_row["keyword"]),
        )
    if kw_rows:
        corpus_conn.commit()

    _auto_resolve_tokens(corpus_conn, pattern_rules, rejected_tokens)

    duration = time.monotonic() - start
    update_pipeline_checkpoint(
        corpus_conn,
        stage="normalize",
        files_processed=total,
        duration_seconds=duration,
    )
    corpus_conn.close()
    progress.done()
