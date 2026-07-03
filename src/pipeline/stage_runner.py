"""Shared stage iteration helper — cancel, progress, and per-item error handling."""
import logging
import threading
from collections.abc import Callable, Sequence
from typing import Any

from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)


def run_stage_loop(
    pending: Sequence,
    process: Callable[[Any], None],
    progress: ProgressReporter,
    cancel_event: threading.Event,
    *,
    label: str = "stage",
) -> tuple[int, int]:
    """Iterate over pending, calling process(row) for each item.

    - Checks cancel_event.is_set() before each item; breaks on cancel.
    - Calls progress.update(i+1, total) before process(row).
    - Catches and logs per-item exceptions; does not propagate them.
    - Calls progress.done() unconditionally on exit (even on cancel).
    - Returns (processed, errors): processed counts items where process()
      succeeded; errors counts items where it raised.
    """
    total = len(pending)
    processed = 0
    errors = 0
    try:
        for i, row in enumerate(pending):
            if cancel_event.is_set():
                break
            progress.update(i + 1, total)
            try:
                process(row)
                processed += 1
            except Exception as e:
                errors += 1
                try:
                    path = row["path"]
                except (KeyError, TypeError):
                    path = i
                logger.error("%s: error on %s: %s", label, path, e)
    finally:
        progress.done()
    return processed, errors
