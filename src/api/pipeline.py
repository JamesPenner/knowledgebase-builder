import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from src.pipeline.progress import SseProgressReporter, get_progress, init_progress, is_running

logger = logging.getLogger(__name__)
router = APIRouter()

_active_cancels: dict[str, threading.Event] = {}


class RunRequest(BaseModel):
    kb: str
    workers: int | None = None
    run_mode: str = "resume"   # resume | rerun
    source_id: int | None = None
    folder_prefix: str | None = None
    file_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    name_pattern: str | None = None


def _get_kb_folder(kb: str) -> Path:
    from src.db.registry import get_kb_path, open_registry
    reg = open_registry(Path("."))
    return get_kb_path(reg, kb)


def _make_stage_routes(stage: str, runner_fn):
    @router.post(f"/{stage}/run", tags=["pipeline"])
    def _run(req: RunRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
        if is_running(req.kb, stage):
            raise HTTPException(status_code=409, detail=f"{stage} is already running for {req.kb}")

        job_id = str(uuid.uuid4())
        cancel = threading.Event()
        _active_cancels[(req.kb, stage)] = cancel

        folder = _get_kb_folder(req.kb)
        corpus_path = folder / "corpus.db"
        kb_path = folder / "knowledge.db"

        from pathlib import Path as P
        from src.config import load_config
        config = load_config(P("config.yaml") if P("config.yaml").exists() else None)
        progress = SseProgressReporter(req.kb, stage)
        init_progress(req.kb, stage)

        from src.pipeline.filter_spec import CorpusFilterSpec
        scope = CorpusFilterSpec(
            source_id=req.source_id,
            folder_prefix=req.folder_prefix,
            file_type=req.file_type,
            date_from=req.date_from,
            date_to=req.date_to,
            name_pattern=req.name_pattern,
        )
        run_mode = req.run_mode

        def _wrapped(cp=corpus_path, kp=kb_path, cfg=config, pr=progress, ce=cancel, sc=scope, rm=run_mode):
            try:
                runner_fn(cp, kp, cfg, pr, ce, scope=sc, run_mode=rm)
            except Exception as exc:
                logger.error("Stage %s failed: %s", stage, exc, exc_info=True)
                pr.failed(str(exc)[:300])

        background_tasks.add_task(_wrapped)
        return {"job_id": job_id, "status": "started"}

    @router.post(f"/{stage}/cancel", tags=["pipeline"])
    def _cancel(kb: str = Query(...)) -> dict[str, str]:
        ev = _active_cancels.get((kb, stage))
        if ev:
            ev.set()
        return {"status": "cancelled"}

    @router.get(f"/{stage}/status", tags=["pipeline"])
    def _status(kb: str = Query(...)) -> dict[str, Any]:
        return get_progress(kb, stage) or {"status": "idle", "current": 0, "total": 0, "rate": 0.0, "eta": 0}

    @router.get(f"/{stage}/stream", tags=["pipeline"])
    async def _stream(kb: str = Query(...)):
        import asyncio
        from fastapi.responses import StreamingResponse

        async def _gen():
            import json
            state = get_progress(kb, stage) or {"status": "idle"}
            yield f"data: {json.dumps(state)}\n\n"
            while state.get("status") == "running":
                await asyncio.sleep(0.5)
                state = get_progress(kb, stage) or {}
                yield f"data: {json.dumps(state)}\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")


def _analyse_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    from src.stages.analyse import run_analyse
    run_analyse(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _normalize_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.normalize import run_normalize
    run_normalize(corpus_path, kb_path, config, progress, cancel)


def _extract_meta_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_file_exif
        conn = open_corpus(corpus_path)
        reset_file_exif(conn)
        conn.close()
    from src.stages.extract_meta import run_extract_meta
    run_extract_meta(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _extract_fields_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_file_fields
        conn = open_corpus(corpus_path)
        reset_file_fields(conn)
        conn.close()
    from src.stages.extract_fields import run_extract_fields
    run_extract_fields(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _hash_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_file_hashes
        conn = open_corpus(corpus_path)
        reset_file_hashes(conn)
        conn.close()
    from src.stages.hash import run_hash
    run_hash(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _aesthetic_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_aesthetic_scores
        conn = open_corpus(corpus_path)
        reset_aesthetic_scores(conn)
        conn.close()
    from src.stages.aesthetic import run_aesthetic
    run_aesthetic(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _describe_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_describe_to_pending
        conn = open_corpus(corpus_path)
        reset_describe_to_pending(conn)
        conn.close()
    from src.stages.describe import run_describe
    run_describe(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _transcribe_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_transcribe_to_pending
        conn = open_corpus(corpus_path)
        reset_transcribe_to_pending(conn)
        conn.close()
    from src.stages.transcribe import run_transcribe
    run_transcribe(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _entity_match_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    from src.stages.entity_match import run_entity_match
    run_entity_match(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _classify_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, **_):
    from src.stages.classify import run_classify
    run_classify(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _temporal_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_temporal_fields
        conn = open_corpus(corpus_path)
        reset_temporal_fields(conn)
        conn.close()
    from src.stages.temporal import run_temporal
    run_temporal(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _retag_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_retag_to_pending
        conn = open_corpus(corpus_path)
        reset_retag_to_pending(conn)
        conn.close()
    from src.stages.retag import run_retag
    run_retag(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _writeback_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.writeback import run_writeback
    run_writeback(corpus_path, kb_path, config, progress, cancel)


def _face_runner(corpus_path, kb_path, config, progress, cancel, **_):
    from src.stages.face import run_face
    run_face(corpus_path, kb_path, config, progress, cancel)


def _face_meta_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_meta_face_regions
        conn = open_corpus(corpus_path)
        reset_meta_face_regions(conn)
        conn.close()
    from src.stages.face_meta import run_face_meta
    run_face_meta(corpus_path, kb_path, config, progress, cancel, scope=scope)


def _geo_meta_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_location_labels
        conn = open_corpus(corpus_path)
        reset_location_labels(conn)
        conn.close()
    from src.stages.geo_meta import run_geo_meta
    run_geo_meta(corpus_path, kb_path, config, progress, cancel, scope=scope)


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
    source_id: int | None = None
    folder_prefix: str | None = None
    file_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    name_pattern: str | None = None


@router.post("/export/run", tags=["pipeline"])
def export_run(req: ExportRunRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    if is_running(req.kb, "export"):
        raise HTTPException(status_code=409, detail=f"export is already running for {req.kb}")

    cancel = threading.Event()
    _active_cancels[(req.kb, "export")] = cancel

    folder = _get_kb_folder(req.kb)
    corpus_path = folder / "corpus.db"
    kb_path = folder / "knowledge.db"

    from pathlib import Path as P
    from src.config import load_config
    from src.pipeline.filter_spec import CorpusFilterSpec
    from src.stages.export import run_export
    config = load_config(P("config.yaml") if P("config.yaml").exists() else None)
    progress = SseProgressReporter(req.kb, "export")
    init_progress(req.kb, "export")
    scope = CorpusFilterSpec(
        source_id=req.source_id,
        folder_prefix=req.folder_prefix,
        file_type=req.file_type,
        date_from=req.date_from,
        date_to=req.date_to,
        name_pattern=req.name_pattern,
    )

    def _wrapped(cp=corpus_path, kp=kb_path, cfg=config, pr=progress, ce=cancel, sec=req.section, sc=scope):
        try:
            run_export(cp, kp, cfg, pr, ce, sec, scope=sc)
        except Exception as exc:
            logger.error("Stage export failed: %s", exc, exc_info=True)
            pr.failed(str(exc)[:300])

    background_tasks.add_task(_wrapped)
    return {"status": "started"}


@router.post("/export/cancel", tags=["pipeline"])
def export_cancel(kb: str = Query(...)) -> dict[str, str]:
    ev = _active_cancels.get((kb, "export"))
    if ev:
        ev.set()
    return {"status": "cancelled"}


@router.get("/export/status", tags=["pipeline"])
def export_status(kb: str = Query(...)) -> dict[str, Any]:
    return get_progress(kb, "export") or {"status": "idle", "current": 0, "total": 0, "rate": 0.0, "eta": 0}


@router.get("/export/stream", tags=["pipeline"])
async def export_stream(kb: str = Query(...)):
    import asyncio
    import json
    from fastapi.responses import StreamingResponse

    async def _gen():
        state = get_progress(kb, "export") or {"status": "idle"}
        yield f"data: {json.dumps(state)}\n\n"
        while state.get("status") == "running":
            await asyncio.sleep(0.5)
            state = get_progress(kb, "export") or {}
            yield f"data: {json.dumps(state)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


def _quality_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_quality_scores
        conn = open_corpus(corpus_path)
        reset_quality_scores(conn)
        conn.close()
    from src.stages.quality import run_quality
    run_quality(corpus_path, kb_path, config, progress, cancel, scope=scope)


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
_make_stage_routes("geo_meta", _geo_meta_runner)
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
    folder_prefix: str | None = None
    file_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    name_pattern: str | None = None


@router.post("/summarize/run", tags=["pipeline"])
def summarize_run(req: SummarizeRunRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    if is_running(req.kb, "summarize"):
        raise HTTPException(status_code=409, detail=f"summarize is already running for {req.kb}")

    job_id = str(uuid.uuid4())
    cancel = threading.Event()
    _active_cancels[(req.kb, "summarize")] = cancel

    folder = _get_kb_folder(req.kb)
    corpus_path = folder / "corpus.db"
    kb_path = folder / "knowledge.db"

    from pathlib import Path as P
    from src.config import load_config
    config = load_config(P("config.yaml") if P("config.yaml").exists() else None)
    progress = SseProgressReporter(req.kb, "summarize")
    init_progress(req.kb, "summarize")

    from src.pipeline.filter_spec import CorpusFilterSpec as _CFS
    _scope = _CFS(
        source_id=req.source_id,
        folder_prefix=req.folder_prefix,
        file_type=req.file_type,
        date_from=req.date_from,
        date_to=req.date_to,
        name_pattern=req.name_pattern,
    )

    def _runner(cp=corpus_path, kp=kb_path, cfg=config, pr=progress, ce=cancel, sc=_scope):
        try:
            if req.force or req.run_mode == "rerun":
                from src.db.corpus import open_corpus, reset_summarize_to_pending
                conn = open_corpus(cp)
                reset_summarize_to_pending(conn)
                conn.close()
            from src.stages.summarize import run_summarize
            run_summarize(cp, kp, cfg, pr, ce, scope=sc)
        except Exception as exc:
            logger.error("Stage summarize failed: %s", exc, exc_info=True)
            pr.failed(str(exc)[:300])

    background_tasks.add_task(_runner)
    return {"job_id": job_id, "status": "started"}


@router.post("/summarize/cancel", tags=["pipeline"])
def summarize_cancel(kb: str = Query(...)) -> dict[str, str]:
    ev = _active_cancels.get((kb, "summarize"))
    if ev:
        ev.set()
    return {"status": "cancelled"}


@router.get("/summarize/status", tags=["pipeline"])
def summarize_status(kb: str = Query(...)) -> dict[str, Any]:
    return get_progress(kb, "summarize") or {"status": "idle", "current": 0, "total": 0, "rate": 0.0, "eta": 0}


@router.get("/summarize/stream", tags=["pipeline"])
async def summarize_stream(kb: str = Query(...)):
    import asyncio
    import json
    from fastapi.responses import StreamingResponse

    async def _gen():
        state = get_progress(kb, "summarize") or {"status": "idle"}
        yield f"data: {json.dumps(state)}\n\n"
        while state.get("status") == "running":
            await asyncio.sleep(0.5)
            state = get_progress(kb, "summarize") or {}
            yield f"data: {json.dumps(state)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


# Suggest has a custom `levels` parameter — built manually instead of via _make_stage_routes

class SuggestRunRequest(BaseModel):
    kb: str
    levels: list[str] = ["a", "b"]


@router.post("/suggest/run", tags=["pipeline"])
def suggest_run(req: SuggestRunRequest, background_tasks: BackgroundTasks) -> dict:
    if is_running(req.kb, "suggest"):
        raise HTTPException(status_code=409, detail=f"suggest is already running for {req.kb}")

    cancel = threading.Event()
    _active_cancels[(req.kb, "suggest")] = cancel

    folder = _get_kb_folder(req.kb)
    corpus_path = folder / "corpus.db"
    kb_path = folder / "knowledge.db"

    from pathlib import Path as P
    from src.config import load_config
    from src.stages.suggest import run_suggest
    config = load_config(P("config.yaml") if P("config.yaml").exists() else None)
    progress = SseProgressReporter(req.kb, "suggest")
    init_progress(req.kb, "suggest")

    def _wrapped(cp=corpus_path, kp=kb_path, cfg=config, pr=progress, ce=cancel, lv=req.levels):
        try:
            run_suggest(cp, kp, cfg, pr, ce, lv)
        except Exception as exc:
            logger.error("Stage suggest failed: %s", exc, exc_info=True)
            pr.failed(str(exc)[:300])

    background_tasks.add_task(_wrapped)
    return {"status": "started"}


@router.post("/suggest/cancel", tags=["pipeline"])
def suggest_cancel(kb: str = Query(...)) -> dict:
    ev = _active_cancels.get((kb, "suggest"))
    if ev:
        ev.set()
    return {"status": "cancelled"}


@router.get("/suggest/status", tags=["pipeline"])
def suggest_status(kb: str = Query(...)) -> dict:
    return get_progress(kb, "suggest") or {"status": "idle", "current": 0, "total": 0, "rate": 0.0, "eta": 0}


@router.get("/suggest/stream", tags=["pipeline"])
async def suggest_stream(kb: str = Query(...)):
    import asyncio
    import json
    from fastapi.responses import StreamingResponse

    async def _gen():
        state = get_progress(kb, "suggest") or {"status": "idle"}
        yield f"data: {json.dumps(state)}\n\n"
        while state.get("status") == "running":
            await asyncio.sleep(0.5)
            state = get_progress(kb, "suggest") or {}
            yield f"data: {json.dumps(state)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


def _ingest_runner(corpus_path, kb_path, config, progress, cancel, *, scope=None, run_mode="resume", **_):
    if run_mode == "rerun":
        from src.db.corpus import open_corpus, reset_corpus_files
        conn = open_corpus(corpus_path)
        reset_corpus_files(conn)
        conn.close()
    from src.stages.ingest import run_ingest
    run_ingest(corpus_path, kb_path, config, progress, cancel)


_make_stage_routes("ingest", _ingest_runner)


class SeedRequest(BaseModel):
    kb: str


_GATE_STYLE = "margin:0;border-radius:0;border-left:none;border-right:none"
_SEED_BTN_LOC = (
    '<button class="btn btn--sm" '
    'hx-post="/api/stages/seed-locations?kb={kb}" '
    'hx-target="#gate-geo_meta" hx-swap="outerHTML">Seed Locations</button>'
)
_SEED_BTN_PPL = (
    '<button class="btn btn--sm" '
    'hx-post="/api/stages/seed-people?kb={kb}" '
    'hx-target="#gate-face_meta" hx-swap="outerHTML">Seed People</button>'
)


def _try_open_folder(path: Path) -> None:
    import os
    import platform
    import subprocess
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(path))
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def _gate_row(row_id: str, cls: str, icon: str, body: str) -> str:
    return (
        f'<tr id="{row_id}"><td colspan="6" style="padding:0">'
        f'<div class="wb-gate {cls}" style="{_GATE_STYLE}">'
        f'<span class="wb-gate-status">{icon}</span>{body}</div></td></tr>'
    )


@router.post("/seed-locations", tags=["pipeline"])
def seed_locations_endpoint(kb: str):
    from fastapi.responses import HTMLResponse
    folder = _get_kb_folder(kb)
    csv_path = folder / "reference" / "registers" / "Index_of_Locations.csv"
    if not csv_path.exists():
        body = (
            "Index_of_Locations.csv not found — "
            f'<button class="btn btn--sm" hx-post="/api/stages/generate-location-template?kb={kb}" '
            f'hx-target="#gate-geo_meta" hx-swap="outerHTML">Generate template</button>'
        )
        return HTMLResponse(_gate_row("gate-geo_meta", "wb-gate--pending", "⚠", body))
    from src.db.kb import open_kb, seed_location_register
    kb_conn = open_kb(folder / "knowledge.db")
    try:
        n = seed_location_register(kb_conn, csv_path)
    finally:
        kb_conn.close()
    msg = "Location register already seeded." if n == 0 else f"✓ {n} location{'s' if n != 1 else ''} seeded."
    return HTMLResponse(_gate_row("gate-geo_meta", "wb-gate--done", "✓", msg))


@router.post("/seed-people", tags=["pipeline"])
def seed_people_endpoint(kb: str):
    from fastapi.responses import HTMLResponse
    folder = _get_kb_folder(kb)
    csv_path = folder / "reference" / "registers" / "Index_of_People.csv"
    if not csv_path.exists():
        body = (
            "Index_of_People.csv not found — "
            f'<button class="btn btn--sm" hx-post="/api/stages/generate-people-template?kb={kb}" '
            f'hx-target="#gate-face_meta" hx-swap="outerHTML">Generate template</button>'
        )
        return HTMLResponse(_gate_row("gate-face_meta", "wb-gate--pending", "⚠", body))
    from src.db.kb import open_kb, seed_people_register
    kb_conn = open_kb(folder / "knowledge.db")
    try:
        n = seed_people_register(kb_conn, csv_path)
    finally:
        kb_conn.close()
    msg = "People register already seeded." if n == 0 else f"✓ {n} {'person' if n == 1 else 'people'} seeded."
    return HTMLResponse(_gate_row("gate-face_meta", "wb-gate--done", "✓", msg))


_LOCATION_CSV_HEADERS = [
    "Location", "City", "State", "Country", "Country Code",
    "Latitude", "Longitude", "threshold_m",
]
_LOCATION_CSV_EXAMPLE = [
    "Home", "London", "England", "United Kingdom", "GB", "51.5074", "-0.1278", "200",
]

_PEOPLE_CSV_HEADERS = [
    "NameID", "First Name", "Last Name", "Title", "Middle Name",
    "Nick Names", "Prefer NickName", "Metadata Name", "Married Names",
    "Family", "SpouseID", "birth_date", "date_marriage", "death_date",
]
_PEOPLE_CSV_EXAMPLE = [
    "P001", "Jane", "Doe", "", "", "", "FALSE", "Jane Doe", "", "FALSE", "", "", "", "",
]


def _write_template_csv(path: Path, headers: list[str], example: list[str]) -> None:
    import csv as _csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = _csv.writer(fh)
        writer.writerow(headers)
        writer.writerow(example)


@router.post("/generate-location-template", tags=["pipeline"])
def generate_location_template_endpoint(kb: str):
    from fastapi.responses import HTMLResponse
    folder = _get_kb_folder(kb)
    csv_path = folder / "reference" / "registers" / "Index_of_Locations.csv"
    if not csv_path.exists():
        _write_template_csv(csv_path, _LOCATION_CSV_HEADERS, _LOCATION_CSV_EXAMPLE)
        _try_open_folder(csv_path.parent)
    rel = "reference/registers/Index_of_Locations.csv"
    body = (
        f"Template created at <code>{rel}</code> — edit it, then "
        + _SEED_BTN_LOC.format(kb=kb)
    )
    return HTMLResponse(_gate_row("gate-geo_meta", "wb-gate--pending", "⚠", body))


@router.post("/generate-people-template", tags=["pipeline"])
def generate_people_template_endpoint(kb: str):
    from fastapi.responses import HTMLResponse
    folder = _get_kb_folder(kb)
    csv_path = folder / "reference" / "registers" / "Index_of_People.csv"
    if not csv_path.exists():
        _write_template_csv(csv_path, _PEOPLE_CSV_HEADERS, _PEOPLE_CSV_EXAMPLE)
        _try_open_folder(csv_path.parent)
    rel = "reference/registers/Index_of_People.csv"
    body = (
        f"Template created at <code>{rel}</code> — edit it, then "
        + _SEED_BTN_PPL.format(kb=kb)
    )
    return HTMLResponse(_gate_row("gate-face_meta", "wb-gate--pending", "⚠", body))


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
