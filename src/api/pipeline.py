import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from src.pipeline.progress import SseProgressReporter, get_progress, init_progress

logger = logging.getLogger(__name__)
router = APIRouter()

_active_cancels: dict[str, threading.Event] = {}


class RunRequest(BaseModel):
    kb: str
    workers: int | None = None
    run_mode: str = "resume"   # resume | rerun
    source_id: int | None = None
    file_type: str | None = None
    set_id: int | None = None


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
        init_progress(stage)

        scope = {
            "run_mode": req.run_mode,
            "source_id": req.source_id,
            "file_type": req.file_type,
            "set_id": req.set_id,
        }

        def _wrapped(cp=corpus_path, kp=kb_path, cfg=config, pr=progress, ce=cancel, sc=scope):
            try:
                runner_fn(cp, kp, cfg, pr, ce, scope=sc)
            except Exception as exc:
                logger.error("Stage %s failed: %s", stage, exc, exc_info=True)
                pr.failed(str(exc)[:300])

        background_tasks.add_task(_wrapped)
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


def _analyse_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.analyse import run_analyse
    run_analyse(corpus_path, kb_path, config, progress, cancel)


def _normalize_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.normalize import run_normalize
    run_normalize(corpus_path, kb_path, config, progress, cancel)


def _extract_meta_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    sc = scope or {}
    if sc.get("run_mode") == "rerun":
        from src.db.corpus import open_corpus, reset_file_exif
        conn = open_corpus(corpus_path)
        reset_file_exif(conn)
        conn.close()
    from src.stages.extract_meta import run_extract_meta
    run_extract_meta(corpus_path, kb_path, config, progress, cancel)


def _extract_fields_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    sc = scope or {}
    if sc.get("run_mode") == "rerun":
        from src.db.corpus import open_corpus, reset_file_fields
        conn = open_corpus(corpus_path)
        reset_file_fields(conn)
        conn.close()
    from src.stages.extract_fields import run_extract_fields
    run_extract_fields(corpus_path, kb_path, config, progress, cancel)


def _hash_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    sc = scope or {}
    if sc.get("run_mode") == "rerun":
        from src.db.corpus import open_corpus, reset_file_hashes
        conn = open_corpus(corpus_path)
        reset_file_hashes(conn)
        conn.close()
    from src.stages.hash import run_hash
    run_hash(corpus_path, kb_path, config, progress, cancel)


def _aesthetic_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    sc = scope or {}
    mode = sc.get("run_mode", "resume")
    source_id = sc.get("source_id")
    file_type = sc.get("file_type")
    set_id = sc.get("set_id")
    if mode == "rerun":
        from src.db.corpus import open_corpus, reset_aesthetic_scores
        conn = open_corpus(corpus_path)
        reset_aesthetic_scores(conn)
        conn.close()
    from src.stages.aesthetic import run_aesthetic
    run_aesthetic(corpus_path, kb_path, config, progress, cancel, source_id=source_id, file_type=file_type, set_id=set_id)


def _describe_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    sc = scope or {}
    mode = sc.get("run_mode", "resume")
    source_id = sc.get("source_id")
    file_type = sc.get("file_type")
    set_id = sc.get("set_id")
    if mode == "rerun":
        from src.db.corpus import open_corpus, reset_describe_to_pending
        conn = open_corpus(corpus_path)
        reset_describe_to_pending(conn)
        conn.close()
    from src.stages.describe import run_describe
    run_describe(corpus_path, kb_path, config, progress, cancel, source_id=source_id, file_type=file_type, set_id=set_id)


def _transcribe_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    sc = scope or {}
    mode = sc.get("run_mode", "resume")
    source_id = sc.get("source_id")
    file_type = sc.get("file_type")
    set_id = sc.get("set_id")
    if mode == "rerun":
        from src.db.corpus import open_corpus, reset_transcribe_to_pending
        conn = open_corpus(corpus_path)
        reset_transcribe_to_pending(conn)
        conn.close()
    from src.stages.transcribe import run_transcribe
    run_transcribe(corpus_path, kb_path, config, progress, cancel, source_id=source_id, file_type=file_type, set_id=set_id)


def _entity_match_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.entity_match import run_entity_match
    run_entity_match(corpus_path, kb_path, config, progress, cancel)


def _classify_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.classify import run_classify
    run_classify(corpus_path, kb_path, config, progress, cancel)


def _temporal_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    sc = scope or {}
    if sc.get("run_mode") == "rerun":
        from src.db.corpus import open_corpus, reset_temporal_fields
        conn = open_corpus(corpus_path)
        reset_temporal_fields(conn)
        conn.close()
    from src.stages.temporal import run_temporal
    run_temporal(corpus_path, kb_path, config, progress, cancel)


def _retag_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    sc = scope or {}
    mode = sc.get("run_mode", "resume")
    source_id = sc.get("source_id")
    set_id = sc.get("set_id")
    if mode == "rerun":
        from src.db.corpus import open_corpus, reset_retag_to_pending
        conn = open_corpus(corpus_path)
        reset_retag_to_pending(conn)
        conn.close()
    from src.stages.retag import run_retag
    run_retag(corpus_path, kb_path, config, progress, cancel, source_id=source_id, set_id=set_id)


def _writeback_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.writeback import run_writeback
    run_writeback(corpus_path, kb_path, config, progress, cancel)


def _face_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.face import run_face
    run_face(corpus_path, kb_path, config, progress, cancel)


def _face_meta_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    sc = scope or {}
    if sc.get("run_mode") == "rerun":
        from src.db.corpus import open_corpus, reset_meta_face_regions
        conn = open_corpus(corpus_path)
        reset_meta_face_regions(conn)
        conn.close()
    from src.stages.face_meta import run_face_meta
    run_face_meta(corpus_path, kb_path, config, progress, cancel, scope=sc)


def _voice_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.voice import run_voice
    run_voice(corpus_path, kb_path, config, progress, cancel)


def _voice_diarize_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.voice import run_voice_diarize
    run_voice_diarize(corpus_path, kb_path, config, progress, cancel)


def _attribute_speakers_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.attribute_speakers import run_attribute_speakers
    run_attribute_speakers(corpus_path, kb_path, config, progress, cancel)


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
    init_progress("export")

    def _wrapped(cp=corpus_path, kp=kb_path, cfg=config, pr=progress, ce=cancel, sec=req.section):
        try:
            run_export(cp, kp, cfg, pr, ce, sec)
        except Exception as exc:
            logger.error("Stage export failed: %s", exc, exc_info=True)
            pr.failed(str(exc)[:300])

    background_tasks.add_task(_wrapped)
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


def _quality_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    sc = scope or {}
    mode = sc.get("run_mode", "resume")
    source_id = sc.get("source_id")
    file_type = sc.get("file_type")
    set_id = sc.get("set_id")
    if mode == "rerun":
        from src.db.corpus import open_corpus, reset_quality_scores
        conn = open_corpus(corpus_path)
        reset_quality_scores(conn)
        conn.close()
    from src.stages.quality import run_quality
    run_quality(corpus_path, kb_path, config, progress, cancel, source_id=source_id, file_type=file_type, set_id=set_id)


def _geolocate_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.geolocate import run_geolocate
    run_geolocate(corpus_path, kb_path, config, progress, cancel)


def _validate_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.validate import run_validate
    run_validate(corpus_path, kb_path.parent, progress, cancel)


_make_stage_routes("quality", _quality_runner)
_make_stage_routes("geolocate", _geolocate_runner)
_make_stage_routes("validate", _validate_runner)
_make_stage_routes("aesthetic", _aesthetic_runner)
_make_stage_routes("face", _face_runner)
_make_stage_routes("face_meta", _face_meta_runner)
_make_stage_routes("voice", _voice_runner)
_make_stage_routes("voice_diarize", _voice_diarize_runner)
_make_stage_routes("attribute_speakers", _attribute_speakers_runner)
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
    run_mode: str = "resume"
    source_id: int | None = None
    set_id: int | None = None


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
    init_progress("summarize")

    def _runner(cp=corpus_path, kp=kb_path, cfg=config, pr=progress, ce=cancel):
        try:
            if req.force or req.run_mode == "rerun":
                from src.db.corpus import open_corpus, reset_summarize_to_pending
                conn = open_corpus(cp)
                reset_summarize_to_pending(conn)
                conn.close()
            from src.stages.summarize import run_summarize
            run_summarize(cp, kp, cfg, pr, ce, source_id=req.source_id, set_id=req.set_id)
        except Exception as exc:
            logger.error("Stage summarize failed: %s", exc, exc_info=True)
            pr.failed(str(exc)[:300])

    background_tasks.add_task(_runner)
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
    init_progress("suggest")

    def _wrapped(cp=corpus_path, kp=kb_path, cfg=config, pr=progress, ce=cancel, lv=req.levels):
        try:
            run_suggest(cp, kp, cfg, pr, ce, lv)
        except Exception as exc:
            logger.error("Stage suggest failed: %s", exc, exc_info=True)
            pr.failed(str(exc)[:300])

    background_tasks.add_task(_wrapped)
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
    init_progress("ingest")

    def _wrapped(cp=corpus_path, kp=kb_path, cfg=config, pr=progress, ce=cancel, inc=req.incremental):
        try:
            run_ingest(cp, kp, cfg, pr, ce, inc)
        except Exception as exc:
            logger.error("Stage ingest failed: %s", exc, exc_info=True)
            pr.failed(str(exc)[:300])

    background_tasks.add_task(_wrapped)
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


class ResolvePlanRequest(BaseModel):
    stages: list[str]
    completed: list[str] = []


@router.post("/resolve-plan", tags=["pipeline"])
def resolve_plan_endpoint(req: ResolvePlanRequest) -> dict:
    from fastapi import HTTPException
    from src.pipeline.dag import resolve_plan

    completed = set(req.completed)
    merged: list = []
    seen_entries: set = set()

    for stage in req.stages:
        try:
            plan = resolve_plan(stage, completed)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for entry in plan:
            key = entry if isinstance(entry, str) else entry["touchpoint"]
            if key not in seen_entries:
                seen_entries.add(key)
                merged.append(entry)

    return {"plan": merged}
