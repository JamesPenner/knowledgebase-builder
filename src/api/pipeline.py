import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from src.pipeline.progress import SseProgressReporter, get_progress

router = APIRouter()

_active_cancels: dict[str, threading.Event] = {}


class RunRequest(BaseModel):
    kb: str
    workers: int | None = None


def _get_kb_folder(kb: str) -> Path:
    from src.db.registry import get_kb_path, open_registry
    reg = open_registry(Path("."))
    return get_kb_path(reg, kb)


def _make_stage_routes(stage: str, runner_fn):
    @router.post(f"/{stage}/run", tags=["pipeline"])
    def _run(req: RunRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        cancel = threading.Event()
        _active_cancels[stage] = cancel

        folder = _get_kb_folder(req.kb)
        corpus_path = folder / "corpus.db"
        kb_path = folder / "knowledge.db"

        from pathlib import Path as P
        from src.config import load_config
        config = load_config(P("config.yaml") if P("config.yaml").exists() else None)
        progress = SseProgressReporter(stage)

        background_tasks.add_task(runner_fn, corpus_path, kb_path, config, progress, cancel)
        return {"job_id": job_id, "status": "started"}

    @router.post(f"/{stage}/cancel", tags=["pipeline"])
    def _cancel() -> dict[str, str]:
        ev = _active_cancels.get(stage)
        if ev:
            ev.set()
        return {"status": "cancelled"}

    @router.get(f"/{stage}/status", tags=["pipeline"])
    def _status() -> dict[str, Any]:
        return get_progress(stage) or {"status": "idle", "current": 0, "total": 0, "rate": 0.0, "eta": 0}

    @router.get(f"/{stage}/stream", tags=["pipeline"])
    async def _stream():
        import asyncio
        from fastapi.responses import StreamingResponse

        async def _gen():
            import json
            state = get_progress(stage) or {"status": "idle"}
            yield f"data: {json.dumps(state)}\n\n"
            while state.get("status") == "running":
                await asyncio.sleep(0.5)
                state = get_progress(stage) or {}
                yield f"data: {json.dumps(state)}\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")


def _analyse_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.analyse import run_analyse
    run_analyse(corpus_path, kb_path, config, progress, cancel)


def _normalize_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.normalize import run_normalize
    run_normalize(corpus_path, kb_path, config, progress, cancel)


def _extract_meta_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.extract_meta import run_extract_meta
    run_extract_meta(corpus_path, kb_path, config, progress, cancel)


def _extract_fields_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.extract_fields import run_extract_fields
    run_extract_fields(corpus_path, kb_path, config, progress, cancel)


def _hash_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.hash import run_hash
    run_hash(corpus_path, kb_path, config, progress, cancel)


def _aesthetic_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.aesthetic import run_aesthetic
    run_aesthetic(corpus_path, kb_path, config, progress, cancel)


def _describe_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.describe import run_describe
    run_describe(corpus_path, kb_path, config, progress, cancel)


def _transcribe_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.transcribe import run_transcribe
    run_transcribe(corpus_path, kb_path, config, progress, cancel)


def _entity_match_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.entity_match import run_entity_match
    run_entity_match(corpus_path, kb_path, config, progress, cancel)


def _classify_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.classify import run_classify
    run_classify(corpus_path, kb_path, config, progress, cancel)


def _temporal_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.temporal import run_temporal
    run_temporal(corpus_path, kb_path, config, progress, cancel)


def _retag_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.retag import run_retag
    run_retag(corpus_path, kb_path, config, progress, cancel)


def _writeback_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.writeback import run_writeback
    run_writeback(corpus_path, kb_path, config, progress, cancel)


class ExportRunRequest(BaseModel):
    kb: str
    section: str | None = None


@router.post("/export/run", tags=["pipeline"])
def export_run(req: ExportRunRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    cancel = threading.Event()
    _active_cancels["export"] = cancel

    folder = _get_kb_folder(req.kb)
    corpus_path = folder / "corpus.db"
    kb_path = folder / "knowledge.db"

    from pathlib import Path as P
    from src.config import load_config
    from src.stages.export import run_export
    config = load_config(P("config.yaml") if P("config.yaml").exists() else None)
    progress = SseProgressReporter("export")

    background_tasks.add_task(run_export, corpus_path, kb_path, config, progress, cancel, req.section)
    return {"status": "started"}


@router.post("/export/cancel", tags=["pipeline"])
def export_cancel() -> dict[str, str]:
    ev = _active_cancels.get("export")
    if ev:
        ev.set()
    return {"status": "cancelled"}


@router.get("/export/status", tags=["pipeline"])
def export_status() -> dict[str, Any]:
    return get_progress("export") or {"status": "idle", "current": 0, "total": 0, "rate": 0.0, "eta": 0}


@router.get("/export/stream", tags=["pipeline"])
async def export_stream():
    import asyncio
    import json
    from fastapi.responses import StreamingResponse

    async def _gen():
        state = get_progress("export") or {"status": "idle"}
        yield f"data: {json.dumps(state)}\n\n"
        while state.get("status") == "running":
            await asyncio.sleep(0.5)
            state = get_progress("export") or {}
            yield f"data: {json.dumps(state)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


def _quality_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.quality import run_quality
    run_quality(corpus_path, kb_path, config, progress, cancel)


def _geolocate_runner(corpus_path, kb_path, config, progress, cancel):
    from src.stages.geolocate import run_geolocate
    run_geolocate(corpus_path, kb_path, config, progress, cancel)


_make_stage_routes("quality", _quality_runner)
_make_stage_routes("geolocate", _geolocate_runner)
_make_stage_routes("aesthetic", _aesthetic_runner)
_make_stage_routes("describe", _describe_runner)
_make_stage_routes("transcribe", _transcribe_runner)
_make_stage_routes("analyse", _analyse_runner)
_make_stage_routes("normalize", _normalize_runner)
_make_stage_routes("extract_meta", _extract_meta_runner)
_make_stage_routes("extract_fields", _extract_fields_runner)
_make_stage_routes("hash", _hash_runner)
_make_stage_routes("entity_match", _entity_match_runner)
_make_stage_routes("classify", _classify_runner)
_make_stage_routes("temporal", _temporal_runner)
_make_stage_routes("retag", _retag_runner)
_make_stage_routes("writeback", _writeback_runner)


# Summarize has a custom `force` parameter — built manually instead of via _make_stage_routes

class SummarizeRunRequest(BaseModel):
    kb: str
    force: bool = False


@router.post("/summarize/run", tags=["pipeline"])
def summarize_run(req: SummarizeRunRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    cancel = threading.Event()
    _active_cancels["summarize"] = cancel

    folder = _get_kb_folder(req.kb)
    corpus_path = folder / "corpus.db"
    kb_path = folder / "knowledge.db"

    from pathlib import Path as P
    from src.config import load_config
    config = load_config(P("config.yaml") if P("config.yaml").exists() else None)
    progress = SseProgressReporter("summarize")

    def _runner(corpus_path, kb_path, config, progress, cancel):
        if req.force:
            from src.db.corpus import open_corpus, reset_summarize_to_pending
            conn = open_corpus(corpus_path)
            reset_summarize_to_pending(conn)
            conn.close()
        from src.stages.summarize import run_summarize
        run_summarize(corpus_path, kb_path, config, progress, cancel)

    background_tasks.add_task(_runner, corpus_path, kb_path, config, progress, cancel)
    return {"job_id": job_id, "status": "started"}


@router.post("/summarize/cancel", tags=["pipeline"])
def summarize_cancel() -> dict[str, str]:
    ev = _active_cancels.get("summarize")
    if ev:
        ev.set()
    return {"status": "cancelled"}


@router.get("/summarize/status", tags=["pipeline"])
def summarize_status() -> dict[str, Any]:
    return get_progress("summarize") or {"status": "idle", "current": 0, "total": 0, "rate": 0.0, "eta": 0}


@router.get("/summarize/stream", tags=["pipeline"])
async def summarize_stream():
    import asyncio
    import json
    from fastapi.responses import StreamingResponse

    async def _gen():
        state = get_progress("summarize") or {"status": "idle"}
        yield f"data: {json.dumps(state)}\n\n"
        while state.get("status") == "running":
            await asyncio.sleep(0.5)
            state = get_progress("summarize") or {}
            yield f"data: {json.dumps(state)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


# Suggest has a custom `levels` parameter — built manually instead of via _make_stage_routes

class SuggestRunRequest(BaseModel):
    kb: str
    levels: list[str] = ["a", "b"]


@router.post("/suggest/run", tags=["pipeline"])
def suggest_run(req: SuggestRunRequest, background_tasks: BackgroundTasks) -> dict:
    cancel = threading.Event()
    _active_cancels["suggest"] = cancel

    folder = _get_kb_folder(req.kb)
    corpus_path = folder / "corpus.db"
    kb_path = folder / "knowledge.db"

    from pathlib import Path as P
    from src.config import load_config
    from src.stages.suggest import run_suggest
    config = load_config(P("config.yaml") if P("config.yaml").exists() else None)
    progress = SseProgressReporter("suggest")

    background_tasks.add_task(run_suggest, corpus_path, kb_path, config, progress, cancel, req.levels)
    return {"status": "started"}


@router.post("/suggest/cancel", tags=["pipeline"])
def suggest_cancel() -> dict:
    ev = _active_cancels.get("suggest")
    if ev:
        ev.set()
    return {"status": "cancelled"}


@router.get("/suggest/status", tags=["pipeline"])
def suggest_status() -> dict:
    return get_progress("suggest") or {"status": "idle", "current": 0, "total": 0, "rate": 0.0, "eta": 0}


@router.get("/suggest/stream", tags=["pipeline"])
async def suggest_stream():
    import asyncio
    import json
    from fastapi.responses import StreamingResponse

    async def _gen():
        state = get_progress("suggest") or {"status": "idle"}
        yield f"data: {json.dumps(state)}\n\n"
        while state.get("status") == "running":
            await asyncio.sleep(0.5)
            state = get_progress("suggest") or {}
            yield f"data: {json.dumps(state)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


class IngestRunRequest(BaseModel):
    kb: str
    workers: int | None = None
    incremental: bool = False


@router.post("/ingest/run", tags=["pipeline"])
def ingest_run(req: IngestRunRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    cancel = threading.Event()
    _active_cancels["ingest"] = cancel
    folder = _get_kb_folder(req.kb)
    corpus_path = folder / "corpus.db"
    kb_path = folder / "knowledge.db"
    from pathlib import Path as P
    from src.config import load_config
    from src.stages.ingest import run_ingest
    config = load_config(P("config.yaml") if P("config.yaml").exists() else None)
    progress = SseProgressReporter("ingest")
    background_tasks.add_task(run_ingest, corpus_path, kb_path, config, progress, cancel, req.incremental)
    return {"job_id": str(uuid.uuid4()), "status": "started"}


@router.post("/ingest/cancel", tags=["pipeline"])
def ingest_cancel() -> dict[str, str]:
    ev = _active_cancels.get("ingest")
    if ev:
        ev.set()
    return {"status": "cancelled"}


@router.get("/ingest/status", tags=["pipeline"])
def ingest_status() -> dict[str, Any]:
    return get_progress("ingest") or {"status": "idle", "current": 0, "total": 0, "rate": 0.0, "eta": 0}


@router.get("/ingest/stream", tags=["pipeline"])
async def ingest_stream():
    import asyncio
    import json
    from fastapi.responses import StreamingResponse

    async def _gen():
        state = get_progress("ingest") or {"status": "idle"}
        yield f"data: {json.dumps(state)}\n\n"
        while state.get("status") == "running":
            await asyncio.sleep(0.5)
            state = get_progress("ingest") or {}
            yield f"data: {json.dumps(state)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")
