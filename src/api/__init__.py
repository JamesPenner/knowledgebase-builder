from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api import aesthetic, field_map, kb, pipeline, progress, review, settings, sources, ui, vocabulary

app = FastAPI(title="KB Builder", version="0.0.1")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(pipeline.router,   prefix="/api/stages",    tags=["pipeline"])
app.include_router(kb.router,         prefix="/api/kb",        tags=["kb"])
app.include_router(review.router,     prefix="/api/review",    tags=["review"])
app.include_router(vocabulary.router, prefix="/api/vocabulary", tags=["vocabulary"])
app.include_router(progress.router,   prefix="/api/progress",  tags=["progress"])
app.include_router(settings.router,   prefix="/api/settings",  tags=["settings"])
app.include_router(sources.router,    prefix="/api/sources",   tags=["sources"])
app.include_router(field_map.router,  prefix="/api/field-map", tags=["field-map"])
app.include_router(aesthetic.router,  prefix="/api/aesthetic", tags=["aesthetic"])
app.include_router(ui.router)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "version": "0.0.1"}
