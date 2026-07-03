import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

from src.config import Config
from src.db.corpus import (
    delete_stale_analyse_tokens,
    get_files_for_analyse,
    open_corpus,
    update_pipeline_checkpoint,
    upsert_analyse_token,
)
from src.pipeline.progress import ProgressReporter

_CAMELCASE_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_ROUTE_CODE_RE = re.compile(r"^[A-Z]{1,4}-\d+$")
_SEQUENTIAL_RE = re.compile(r"^\d{3,4}$")
_WORD_RE = re.compile(r"^[A-Za-z0-9]+$")


def detect_common_prefix(paths: list[str]) -> str:
    """Return the longest common filesystem path prefix across all paths."""
    if not paths:
        return ""
    if len(paths) == 1:
        return str(Path(paths[0]).parent)
    # Use os.path.commonpath which handles path boundaries correctly
    try:
        return os.path.commonpath(paths)
    except ValueError:
        return ""


def tokenize_path(relative_path: str) -> list[str]:
    """Split a relative path into lowercase unique tokens.

    Splits on: _, -, ., space, CamelCase boundaries, and path separators.
    Returns deduplicated list preserving first-occurrence order.
    """
    # Replace path separators with space
    text = relative_path.replace("\\", " ").replace("/", " ").replace(".", " ")

    # Split CamelCase before further splitting
    text = _CAMELCASE_RE.sub(" ", text)

    # Split on common delimiters
    raw_tokens = re.split(r"[-_\s]+", text)

    seen: set[str] = set()
    result: list[str] = []
    for t in raw_tokens:
        lower = t.lower().strip()
        if lower and lower not in seen:
            seen.add(lower)
            result.append(lower)
    return result


def classify_token(token: str) -> tuple[str, str]:
    """Return (pattern_class, semantic_type) for a single token.

    pattern_class: '6digit_numeric' | '8digit_numeric' | 'camelcase' |
                   'route_code' | 'sequential' | 'word'
    semantic_type: 'date' | 'time' | 'sequential' | 'code' | 'compound' | 'unclassified' | 'word'
    """
    # Route code: LETTERS-digits (tested on original token, not lower)
    if _ROUTE_CODE_RE.match(token):
        return ("route_code", "code")

    lower = token.lower()

    # CamelCase: original token has interior uppercase after a lowercase
    if token != lower and _CAMELCASE_RE.search(token):
        return ("camelcase", "compound")

    # 8-digit numeric
    if re.match(r"^\d{8}$", lower):
        return _classify_8digit(lower)

    # 6-digit numeric
    if re.match(r"^\d{6}$", lower):
        return _classify_6digit(lower)

    # Sequential counter: 3-4 digits that look like a sequence number
    if _SEQUENTIAL_RE.match(lower) and int(lower) < 10000:
        return ("sequential", "sequential")

    # Plain word
    if _WORD_RE.match(lower):
        return ("word", "word")

    return ("word", "unclassified")


def _classify_6digit(token: str) -> tuple[str, str]:
    mm = int(token[2:4])
    dd = int(token[4:6])
    hh = int(token[0:2])
    mi = int(token[2:4])

    # Date check: YYMMDD — year plausible, month 01-12, day 01-31
    if 1 <= mm <= 12 and 1 <= dd <= 31:
        return ("6digit_numeric", "date")

    # Time check: HHMMSS — hour 00-23, minute 00-59
    if 0 <= hh <= 23 and 0 <= mi <= 59:
        return ("6digit_numeric", "time")

    return ("6digit_numeric", "unclassified")


def _classify_8digit(token: str) -> tuple[str, str]:
    yyyy = int(token[0:4])
    mm = int(token[4:6])
    dd = int(token[6:8])

    if 1900 <= yyyy <= 2099 and 1 <= mm <= 12 and 1 <= dd <= 31:
        return ("8digit_numeric", "date")

    return ("8digit_numeric", "unclassified")


def propose_action(pattern_class: str, semantic_type: str) -> tuple[str, str]:
    """Return (proposed_action, proposed_extract_as)."""
    if semantic_type == "date":
        return ("capture", "file_date")
    if semantic_type == "time":
        return ("capture", "file_time")
    if semantic_type == "code":
        return ("capture", "route_number")
    if semantic_type == "sequential":
        return ("ignore", "")
    if semantic_type == "compound":
        return ("none", "")
    return ("none", "")


def run_analyse(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    *,
    scope=None,
) -> None:
    conn = open_corpus(corpus_path)
    files = get_files_for_analyse(conn, scope=scope)

    if not files:
        progress.done()
        conn.close()
        return

    all_paths = [f["path"] for f in files]
    prefix = detect_common_prefix(all_paths)

    # Build token frequency maps: token → set of file paths, token → total occurrences
    token_files: dict[str, set[int]] = defaultdict(set)
    token_freq: dict[str, int] = defaultdict(int)
    token_depth: dict[str, int] = defaultdict(int)
    bigram_files: dict[str, set[int]] = defaultdict(set)
    bigram_freq: dict[str, int] = defaultdict(int)

    start = time.monotonic()
    total = len(files)

    for i, file_row in enumerate(files):
        if cancel_event.is_set():
            break

        path = file_row["path"]
        # Strip prefix and normalize
        if prefix and path.startswith(prefix):
            relative = path[len(prefix):]
        else:
            relative = path
        relative = relative.lstrip("/\\")

        # Determine depth of file (number of directory components before filename)
        parts = Path(relative).parts
        depth = max(0, len(parts) - 1)  # depth = number of path components minus filename

        tokenize_input = relative if relative else file_row["filename"]

        tokens = tokenize_path(tokenize_input)

        file_id = file_row["id"]
        file_word_tokens: list[str] = []
        for tok in tokens:
            token_files[tok].add(file_id)
            token_freq[tok] += 1
            # Track the primary depth (first time we see it at a given depth; overwrite later)
            if tok not in token_depth:
                token_depth[tok] = depth
            if classify_token(tok)[0] == "word":
                file_word_tokens.append(tok)

        for j in range(len(file_word_tokens) - 1):
            bigram = f"{file_word_tokens[j]} {file_word_tokens[j + 1]}"
            bigram_files[bigram].add(file_id)
            bigram_freq[bigram] += 1

        progress.update(i + 1, total)

    # Check cross-source: tokens that also appear in file_metadata_keywords
    try:
        meta_keywords: set[str] = {
            row[0].lower()
            for row in conn.execute("SELECT DISTINCT keyword FROM file_metadata_keywords").fetchall()
        }
    except Exception:
        meta_keywords = set()

    # Write tokens to DB — upsert preserves 'decided' status on existing rows
    batch_size = 200
    items = list(token_files.items())
    for j in range(0, len(items), batch_size):
        if cancel_event.is_set():
            break
        chunk = items[j : j + batch_size]
        for token, file_id_set in chunk:
            pattern_class, semantic_type = classify_token(token)
            action, extract_as = propose_action(pattern_class, semantic_type)
            is_cross_source = token in meta_keywords
            depth = token_depth.get(token, 0)

            upsert_analyse_token(
                conn,
                token=token,
                pattern_class=pattern_class,
                semantic_type=semantic_type,
                frequency=token_freq[token],
                file_count=len(file_id_set),
                proposed_action=action,
                proposed_extract_as=extract_as,
                is_cross_source=is_cross_source,
                depth_position=depth,
            )
        conn.commit()

    # Write bigrams — only those appearing in >= 2 files
    _MIN_BIGRAM_FILES = 2
    bigram_items = [
        (bg, fids) for bg, fids in bigram_files.items() if len(fids) >= _MIN_BIGRAM_FILES
    ]
    for j in range(0, len(bigram_items), batch_size):
        if cancel_event.is_set():
            break
        chunk = bigram_items[j:j + batch_size]
        for bigram, file_id_set in chunk:
            upsert_analyse_token(
                conn,
                token=bigram,
                pattern_class="ngram",
                semantic_type="compound",
                frequency=bigram_freq[bigram],
                file_count=len(file_id_set),
                proposed_action="none",
                proposed_extract_as="",
                is_cross_source=bigram in meta_keywords,
                depth_position=0,
            )
        conn.commit()

    # Remove tokens that no longer appear in any corpus file
    surviving_bigrams = {bg for bg, fids in bigram_files.items() if len(fids) >= _MIN_BIGRAM_FILES}
    if not cancel_event.is_set():
        delete_stale_analyse_tokens(conn, set(token_files.keys()) | surviving_bigrams)

    duration = time.monotonic() - start
    update_pipeline_checkpoint(
        conn,
        stage="analyse",
        files_processed=total,
        duration_seconds=duration,
    )
    conn.close()
    progress.done()
