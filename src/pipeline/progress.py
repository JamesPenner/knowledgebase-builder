import threading
import time
from typing import Protocol, runtime_checkable

_progress: dict = {}
_progress_lock = threading.Lock()


@runtime_checkable
class ProgressReporter(Protocol):
    def update(self, current: int, total: int, message: str = "") -> None: ...
    def done(self) -> None: ...


class NullProgressReporter:
    def update(self, current: int, total: int, message: str = "") -> None:
        pass

    def done(self) -> None:
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


def get_progress(stage: str) -> dict:
    with _progress_lock:
        return dict(_progress.get(stage, {}))
