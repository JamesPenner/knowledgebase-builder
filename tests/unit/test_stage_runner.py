"""Unit tests for src/pipeline/stage_runner.py — no DB, no filesystem."""
import threading

import pytest

from src.pipeline.progress import NullProgressReporter
from src.pipeline.stage_runner import run_stage_loop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _RecordingProgress:
    """Captures progress.update and progress.done calls for assertion."""
    def __init__(self):
        self.updates = []
        self.done_count = 0
        self.messages = []

    def update(self, current: int, total: int, message: str = "") -> None:
        self.updates.append((current, total))
        if message:
            self.messages.append(message)

    def done(self) -> None:
        self.done_count += 1

    def set_message(self, message: str, total: int = 0) -> None:
        pass


def _rows(n: int) -> list[dict]:
    return [{"id": i, "path": f"/file/{i}.jpg"} for i in range(n)]


def _no_cancel() -> threading.Event:
    return threading.Event()


def _cancelled() -> threading.Event:
    e = threading.Event()
    e.set()
    return e


# ---------------------------------------------------------------------------
# Basic iteration
# ---------------------------------------------------------------------------

class TestRunStageLoopBasic:
    def test_processes_all_items_when_no_cancel(self):
        processed_ids = []
        run_stage_loop(
            _rows(5),
            lambda row: processed_ids.append(row["id"]),
            NullProgressReporter(),
            _no_cancel(),
        )
        assert processed_ids == [0, 1, 2, 3, 4]

    def test_returns_processed_and_zero_errors_on_success(self):
        processed, errors = run_stage_loop(
            _rows(3),
            lambda row: None,
            NullProgressReporter(),
            _no_cancel(),
        )
        assert processed == 3
        assert errors == 0

    def test_empty_pending_returns_zero_zero(self):
        processed, errors = run_stage_loop(
            [],
            lambda row: None,
            NullProgressReporter(),
            _no_cancel(),
        )
        assert processed == 0
        assert errors == 0


# ---------------------------------------------------------------------------
# progress.done() guarantee
# ---------------------------------------------------------------------------

class TestProgressDoneGuarantee:
    def test_done_called_on_normal_completion(self):
        prog = _RecordingProgress()
        run_stage_loop(_rows(3), lambda row: None, prog, _no_cancel())
        assert prog.done_count == 1

    def test_done_called_on_empty_pending(self):
        prog = _RecordingProgress()
        run_stage_loop([], lambda row: None, prog, _no_cancel())
        assert prog.done_count == 1

    def test_done_called_when_cancel_fires_before_start(self):
        prog = _RecordingProgress()
        run_stage_loop(_rows(5), lambda row: None, prog, _cancelled())
        assert prog.done_count == 1

    def test_done_called_when_process_raises(self):
        prog = _RecordingProgress()
        run_stage_loop(
            _rows(3),
            lambda row: (_ for _ in ()).throw(RuntimeError("boom")),
            prog,
            _no_cancel(),
        )
        assert prog.done_count == 1

    def test_done_called_exactly_once(self):
        prog = _RecordingProgress()
        run_stage_loop(_rows(10), lambda row: None, prog, _no_cancel())
        assert prog.done_count == 1


# ---------------------------------------------------------------------------
# Cancel behaviour
# ---------------------------------------------------------------------------

class TestCancelBehaviour:
    def test_cancel_before_start_processes_nothing(self):
        calls = []
        run_stage_loop(_rows(5), lambda row: calls.append(row["id"]), NullProgressReporter(), _cancelled())
        assert calls == []

    def test_cancel_mid_run_stops_further_processing(self):
        cancel = threading.Event()
        calls = []

        def process(row):
            calls.append(row["id"])
            if row["id"] == 2:
                cancel.set()

        run_stage_loop(_rows(6), process, NullProgressReporter(), cancel)
        # Item 2 is processed (cancel set inside), item 3+ are not
        assert 2 in calls
        assert 3 not in calls

    def test_partial_count_returned_on_cancel(self):
        cancel = threading.Event()

        def process(row):
            if row["id"] == 2:
                cancel.set()

        processed, errors = run_stage_loop(_rows(6), process, NullProgressReporter(), cancel)
        assert processed == 3  # items 0, 1, 2 completed
        assert errors == 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_exception_in_process_does_not_propagate(self):
        def always_fails(row):
            raise ValueError("test error")

        # Should not raise
        run_stage_loop(_rows(3), always_fails, NullProgressReporter(), _no_cancel())

    def test_error_count_increments_on_exception(self):
        processed, errors = run_stage_loop(
            _rows(4),
            lambda row: (_ for _ in ()).throw(RuntimeError("fail")) if row["id"] % 2 == 0 else None,
            NullProgressReporter(),
            _no_cancel(),
        )
        assert errors == 2
        assert processed == 2

    def test_processing_continues_after_per_item_error(self):
        calls = []

        def process(row):
            if row["id"] == 1:
                raise ValueError("skip me")
            calls.append(row["id"])

        run_stage_loop(_rows(4), process, NullProgressReporter(), _no_cancel())
        assert calls == [0, 2, 3]

    def test_row_without_path_key_does_not_raise_in_error_handler(self):
        rows = [1, 2, 3]  # plain ints, not dicts

        def always_fails(row):
            raise RuntimeError("bad")

        processed, errors = run_stage_loop(rows, always_fails, NullProgressReporter(), _no_cancel())
        assert errors == 3


# ---------------------------------------------------------------------------
# Progress update correctness
# ---------------------------------------------------------------------------

class TestProgressUpdates:
    def test_progress_update_called_once_per_item(self):
        prog = _RecordingProgress()
        run_stage_loop(_rows(4), lambda row: None, prog, _no_cancel())
        assert len(prog.updates) == 4

    def test_progress_update_uses_one_indexed_current(self):
        prog = _RecordingProgress()
        run_stage_loop(_rows(3), lambda row: None, prog, _no_cancel())
        currents = [u[0] for u in prog.updates]
        assert currents == [1, 2, 3]

    def test_progress_update_total_is_len_pending(self):
        prog = _RecordingProgress()
        run_stage_loop(_rows(5), lambda row: None, prog, _no_cancel())
        totals = {u[1] for u in prog.updates}
        assert totals == {5}

    def test_progress_update_before_process(self):
        """update(i+1, total) fires before process(row) — verified via ordering."""
        order = []
        prog = _RecordingProgress()
        original_update = prog.update

        def tracking_update(current, total, message=""):
            order.append(("update", current))
            original_update(current, total, message)

        prog.update = tracking_update

        def process(row):
            order.append(("process", row["id"]))

        run_stage_loop(_rows(3), process, prog, _no_cancel())
        # For each item: update comes before process
        for i in range(3):
            assert order[i * 2] == ("update", i + 1)
            assert order[i * 2 + 1] == ("process", i)

    def test_no_updates_when_cancelled_before_start(self):
        prog = _RecordingProgress()
        run_stage_loop(_rows(5), lambda row: None, prog, _cancelled())
        assert prog.updates == []
