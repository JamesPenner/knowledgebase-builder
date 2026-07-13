import threading
import time
from typing import Protocol, runtime_checkable

# Keyed by (kb, stage) — not stage alone, so the same stage running against
# two different KBs tracks independent state instead of colliding.
_progress: dict = {}
_progress_lock = threading.Lock()


@runtime_checkable
class ProgressReporter(Protocol):
    def update(self, current: int, total: int, message: str = "") -> None: ...
    def done(self) -> None: ...
    def set_message(self, message: str, total: int = 0) -> None: ...


class NullProgressReporter:
    def update(self, current: int, total: int, message: str = "") -> None:
        pass

    def done(self) -> None:
        pass

    def set_message(self, message: str, total: int = 0) -> None:
        pass


class SseProgressReporter:
    def __init__(self, kb: str, stage: str) -> None:
        self._key = (kb, stage)
        self._start: float | None = None

    def update(self, current: int, total: int, message: str = "") -> None:
        now = time.monotonic()
        if self._start is None:
            self._start = now

        elapsed = now - self._start
        rate = round(current / elapsed, 2) if elapsed > 0 and current > 0 else 0.0
        remaining = total - current
        eta = int(remaining / rate) if rate > 0 else 0

        with _progress_lock:
            _progress[self._key] = {
                "current": current,
                "total": total,
                "rate": rate,
                "eta": eta,
                "status": "running",
                "message": message,
            }

    def done(self) -> None:
        with _progress_lock:
            entry = _progress.get(self._key, {})
            entry["status"] = "done"
            entry["eta"] = 0
            _progress[self._key] = entry

    def set_message(self, message: str, total: int = 0) -> None:
        """Update message and optionally total without starting the rate timer."""
        with _progress_lock:
            entry = _progress.get(self._key, {})
            entry["message"] = message
            if total:
                entry["total"] = total
            _progress[self._key] = entry

    def failed(self, message: str = "") -> None:
        with _progress_lock:
            entry = _progress.get(self._key, {})
            entry["status"] = "failed"
            entry["eta"] = 0
            entry["message"] = message
            _progress[self._key] = entry


def get_progress(kb: str, stage: str) -> dict:
    with _progress_lock:
        return dict(_progress.get((kb, stage), {}))


def init_progress(kb: str, stage: str) -> None:
    """Seed progress to 'running' before the background task starts.

    Without this the SSE stream sees 'idle', the while loop never enters,
    and the stream closes immediately — leaving the UI silent during model load.
    """
    with _progress_lock:
        _progress[(kb, stage)] = {
            "current": 0,
            "total": 0,
            "rate": 0.0,
            "eta": 0,
            "status": "running",
            "message": "Starting…",
        }


def is_running(kb: str, stage: str) -> bool:
    """True if a job for this (kb, stage) is currently in progress."""
    with _progress_lock:
        return _progress.get((kb, stage), {}).get("status") == "running"
