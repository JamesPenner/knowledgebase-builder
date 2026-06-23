import threading
import time
from typing import Protocol, runtime_checkable

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
    def __init__(self, stage: str) -> None:
        self._stage = stage
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
            _progress[self._stage] = {
                "current": current,
                "total": total,
                "rate": rate,
                "eta": eta,
                "status": "running",
                "message": message,
            }

    def done(self) -> None:
        with _progress_lock:
            entry = _progress.get(self._stage, {})
            entry["status"] = "done"
            entry["eta"] = 0
            _progress[self._stage] = entry

    def set_message(self, message: str, total: int = 0) -> None:
        """Update message and optionally total without starting the rate timer."""
        with _progress_lock:
            entry = _progress.get(self._stage, {})
            entry["message"] = message
            if total:
                entry["total"] = total
            _progress[self._stage] = entry

    def failed(self, message: str = "") -> None:
        with _progress_lock:
            entry = _progress.get(self._stage, {})
            entry["status"] = "failed"
            entry["eta"] = 0
            entry["message"] = message
            _progress[self._stage] = entry


def get_progress(stage: str) -> dict:
    with _progress_lock:
        return dict(_progress.get(stage, {}))


def init_progress(stage: str) -> None:
    """Seed progress to 'running' before the background task starts.

    Without this the SSE stream sees 'idle', the while loop never enters,
    and the stream closes immediately — leaving the UI silent during model load.
    """
    with _progress_lock:
        _progress[stage] = {
            "current": 0,
            "total": 0,
            "rate": 0.0,
            "eta": 0,
            "status": "running",
            "message": "Starting…",
        }
