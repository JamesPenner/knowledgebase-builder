import io
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from src.api.deps import resolve_kb

router = APIRouter()

_HX_TRIGGER_CLUSTERS = '{"clustersChanged": null}'
_THUMBNAIL_SIZE = 120


# ---------------------------------------------------------------------------
# People registry (KB.Q4)
# ---------------------------------------------------------------------------

class PersonAddBody(BaseModel):
    preferred_name: str


class PersonUpdateBody(BaseModel):
    preferred_name: str


class PersonMergeBody(BaseModel):
    merge_from_id: int


@router.get("/people")
def list_people(paths: tuple[Path, Path] = Depends(resolve_kb)) -> dict:
    corpus_path, kb_path = paths
    from src.db.corpus import open_corpus
    from src.db.kb import get_people_with_cluster_counts, open_kb
    kb_conn = open_kb(kb_path)
    corpus_conn = open_corpus(corpus_path)
    people = get_people_with_cluster_counts(kb_conn, corpus_conn)
    kb_conn.close()
    corpus_conn.close()
    return {"people": people}


@router.post("/people")
def add_person(
    body: PersonAddBody,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    name = body.preferred_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="preferred_name is required")
    _, kb_path = paths
    from src.db.kb import open_kb
    kb_conn = open_kb(kb_path)
    existing = kb_conn.execute(
        "SELECT id FROM people WHERE preferred_name = ?", (name,)
    ).fetchone()
    if existing:
        kb_conn.close()
        raise HTTPException(status_code=422, detail="Name already exists")
    kb_conn.execute("INSERT INTO people (preferred_name) VALUES (?)", (name,))
    kb_conn.commit()
    row = kb_conn.execute(
        "SELECT id, preferred_name FROM people WHERE preferred_name = ?", (name,)
    ).fetchone()
    result = dict(row)
    kb_conn.close()
    return result


@router.put("/people/{person_id}")
def update_person(
    person_id: int,
    body: PersonUpdateBody,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    name = body.preferred_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="preferred_name is required")
    _, kb_path = paths
    from src.db.kb import open_kb
    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT id FROM people WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        kb_conn.close()
        raise HTTPException(status_code=404, detail="Person not found")
    kb_conn.execute("UPDATE people SET preferred_name = ? WHERE id = ?", (name, person_id))
    kb_conn.commit()
    updated = dict(kb_conn.execute(
        "SELECT id, preferred_name FROM people WHERE id = ?", (person_id,)
    ).fetchone())
    kb_conn.close()
    return updated


@router.delete("/people/{person_id}")
def delete_person_route(
    person_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    corpus_path, kb_path = paths
    from src.db.corpus import open_corpus
    from src.db.kb import delete_person, open_kb
    kb_conn = open_kb(kb_path)
    corpus_conn = open_corpus(corpus_path)
    try:
        delete_person(kb_conn, corpus_conn, person_id)
    except KeyError as exc:
        kb_conn.close()
        corpus_conn.close()
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        kb_conn.close()
        corpus_conn.close()
        raise HTTPException(status_code=422, detail=str(exc))
    kb_conn.close()
    corpus_conn.close()
    return {}


@router.post("/people/{person_id}/merge")
def merge_person_route(
    person_id: int,
    body: PersonMergeBody,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    corpus_path, kb_path = paths
    from src.db.corpus import open_corpus
    from src.db.kb import merge_people, open_kb
    kb_conn = open_kb(kb_path)
    corpus_conn = open_corpus(corpus_path)
    try:
        merge_people(kb_conn, corpus_conn, person_id, body.merge_from_id)
    except ValueError as exc:
        kb_conn.close()
        corpus_conn.close()
        raise HTTPException(status_code=422, detail=str(exc))
    except KeyError as exc:
        kb_conn.close()
        corpus_conn.close()
        raise HTTPException(status_code=404, detail=str(exc))
    kb_conn.close()
    corpus_conn.close()
    return {"merged_into": person_id}


# ---------------------------------------------------------------------------
# Face cluster review (KB.Q3)
# ---------------------------------------------------------------------------

@router.get("/people/faces/clusters")
def list_face_clusters(paths: tuple[Path, Path] = Depends(resolve_kb)) -> dict:
    corpus_path, _ = paths
    from src.db.corpus import (
        get_assigned_face_clusters,
        get_pending_face_clusters,
        open_corpus,
    )
    conn = open_corpus(corpus_path)
    pending_rows = get_pending_face_clusters(conn)
    assigned_rows = get_assigned_face_clusters(conn)
    conn.close()

    def _pending_item(r) -> dict:
        r = dict(r)
        rep = None
        if r.get("rep_face_region_id") is not None:
            rep = {
                "face_region_id": r["rep_face_region_id"],
                "file_path": r["rep_file_path"],
                "thumbnail_url": f"/api/knowledge/corpus/face-thumbnail/{r['rep_face_region_id']}",
            }
        return {"id": r["id"], "member_count": r["member_count"], "spread": r["spread"], "representative": rep}

    def _assigned_item(r) -> dict:
        r = dict(r)
        return {"id": r["id"], "member_count": r["member_count"], "label": r["label"], "person_id": r["person_id"]}

    return {
        "pending": [_pending_item(r) for r in pending_rows],
        "assigned": [_assigned_item(r) for r in assigned_rows],
    }


@router.get("/corpus/face-thumbnail/{face_region_id}")
def face_thumbnail(
    face_region_id: int,
    kb: str = Query(...),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from PIL import Image

    corpus_path, _ = paths
    from src.db.corpus import get_face_region_for_thumbnail, open_corpus
    conn = open_corpus(corpus_path)
    row = get_face_region_for_thumbnail(conn, face_region_id)
    conn.close()

    def _grey_jpeg() -> Response:
        grey = Image.new("RGB", (1, 1), (128, 128, 128))
        buf = io.BytesIO()
        grey.save(buf, format="JPEG")
        return Response(content=buf.getvalue(), media_type="image/jpeg")

    if row is None:
        return _grey_jpeg()

    try:
        bbox = json.loads(row["bbox"]) if row["bbox"] else None
        if bbox is None or len(bbox) < 4:
            return _grey_jpeg()
        x1, y1, x2, y2 = bbox
        PADDING = 10
        img = Image.open(row["file_path"]).convert("RGB")
        box = (
            max(0, int(x1) - PADDING),
            max(0, int(y1) - PADDING),
            min(img.width, int(x2) + PADDING),
            min(img.height, int(y2) + PADDING),
        )
        crop = img.crop(box)
        crop = crop.resize((_THUMBNAIL_SIZE, _THUMBNAIL_SIZE), Image.LANCZOS)
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=85)
        return Response(content=buf.getvalue(), media_type="image/jpeg")
    except Exception:
        return _grey_jpeg()


class RenameBody(BaseModel):
    label: str


class RegistryUpdateBody(BaseModel):
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    threshold_m: float | None = None


class MergeBody(BaseModel):
    table: str
    keep_id: int
    drop_id: int


def _entry_to_dict(row) -> dict:
    d = dict(row)
    for field in ("latitude", "longitude", "threshold_m"):
        val = d.get(field)
        if val is not None and val != "":
            try:
                d[field] = float(val)
            except (TypeError, ValueError):
                d[field] = None
        else:
            d[field] = None
    return d


@router.get("/locations/clusters")
def list_clusters(paths: tuple[Path, Path] = Depends(resolve_kb)) -> dict:
    corpus_path, _ = paths
    from src.db.corpus import get_gps_clusters, open_corpus
    conn = open_corpus(corpus_path)
    clusters = [dict(c) for c in get_gps_clusters(conn)]
    conn.close()
    return {"clusters": clusters}


@router.post("/locations/clusters/{cluster_id}/rename")
def rename_cluster(
    cluster_id: int,
    body: RenameBody,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    corpus_path, _ = paths
    from src.db.corpus import open_corpus, rename_gps_cluster
    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT id FROM gps_clusters WHERE id=?", (cluster_id,)).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Cluster not found")
    rename_gps_cluster(conn, cluster_id, body.label)
    conn.close()
    return Response(
        content=json.dumps({"cluster_id": cluster_id, "label": body.label}),
        media_type="application/json",
        headers={"HX-Trigger": _HX_TRIGGER_CLUSTERS},
    )


@router.post("/locations/clusters/{cluster_id}/promote")
def promote_cluster(
    cluster_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    corpus_path, kb_path = paths
    from src.db.corpus import open_corpus
    corpus_conn = open_corpus(corpus_path)
    row = corpus_conn.execute("SELECT * FROM gps_clusters WHERE id=?", (cluster_id,)).fetchone()
    corpus_conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Cluster not found")

    from src.db.kb import create_entity_table, open_kb, register_entity_table, upsert_entity_row
    kb_conn = open_kb(kb_path)
    create_entity_table(
        kb_conn,
        "gps_cluster_locations",
        ["location", "latitude", "longitude", "threshold_m", "file_count"],
        "location",
    )
    register_entity_table(
        kb_conn,
        table_name="gps_cluster_locations",
        display_name="GPS Cluster Locations",
        trigger_word="",
        trigger_aliases_json="[]",
        key_column="location",
        match_type="gps",
        source_csv="gps_clusters",
    )
    upsert_entity_row(kb_conn, "gps_cluster_locations", {
        "location": row["label"],
        "latitude": str(row["centroid_lat"]),
        "longitude": str(row["centroid_lon"]),
        "threshold_m": str(row["eps_km"] * 1000),
        "file_count": str(row["file_count"]),
    })
    kb_conn.commit()
    kb_conn.close()

    return Response(
        content=json.dumps({
            "status": "promoted",
            "entity_table": "entity_gps_cluster_locations",
            "label": row["label"],
        }),
        media_type="application/json",
        headers={"HX-Trigger": _HX_TRIGGER_CLUSTERS},
    )


# ---------------------------------------------------------------------------
# Location registry (KB.Q2)
# ---------------------------------------------------------------------------

@router.get("/locations/registry")
def list_registry(paths: tuple[Path, Path] = Depends(resolve_kb)) -> dict:
    _, kb_path = paths
    from src.db.kb import (
        find_location_near_duplicates,
        get_entity_location_tables,
        get_entity_table_entries,
        open_kb,
    )
    kb_conn = open_kb(kb_path)
    tables = get_entity_location_tables(kb_conn)
    result = []
    for t in tables:
        entries = [_entry_to_dict(e) for e in get_entity_table_entries(kb_conn, t["name"])]
        near_dups = find_location_near_duplicates(entries)
        result.append({
            "name": t["name"],
            "match_type": t["match_type"],
            "entries": entries,
            "near_duplicates": near_dups,
        })
    kb_conn.close()
    return {"tables": result}


@router.put("/locations/registry/{table}/{entry_id}")
def update_registry_entry(
    table: str,
    entry_id: int,
    body: RegistryUpdateBody,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    _, kb_path = paths
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422, detail="No fields provided")
    str_fields = {k: str(v) for k, v in fields.items()}
    from src.db.kb import open_kb, update_entity_table_entry
    kb_conn = open_kb(kb_path)
    try:
        row = update_entity_table_entry(kb_conn, table, entry_id, str_fields)
    except ValueError as exc:
        kb_conn.close()
        raise HTTPException(status_code=404, detail=str(exc))
    kb_conn.close()
    return _entry_to_dict(row)


@router.delete("/locations/registry/{table}/{entry_id}")
def delete_registry_entry(
    table: str,
    entry_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    _, kb_path = paths
    from src.db.kb import delete_entity_table_entry, open_kb
    kb_conn = open_kb(kb_path)
    try:
        delete_entity_table_entry(kb_conn, table, entry_id)
    except ValueError as exc:
        kb_conn.close()
        raise HTTPException(status_code=404, detail=str(exc))
    kb_conn.close()
    return {}


@router.post("/locations/registry/merge")
def merge_registry_entries(
    body: MergeBody,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    _, kb_path = paths
    if body.keep_id == body.drop_id:
        raise HTTPException(status_code=422, detail="keep_id and drop_id must differ")
    from src.db.kb import merge_entity_table_entries, open_kb
    kb_conn = open_kb(kb_path)
    try:
        merge_entity_table_entries(kb_conn, body.table, body.keep_id, body.drop_id)
    except ValueError as exc:
        kb_conn.close()
        raise HTTPException(status_code=404, detail=str(exc))
    kb_conn.close()
    return {"merged_into": body.keep_id, "table": body.table}


# ---------------------------------------------------------------------------
# Prompt library (KB.S5)
# ---------------------------------------------------------------------------

class PromptCreateBody(BaseModel):
    stage: str
    prompt_key: str
    name: str
    body: str


class PromptUpdateBody(BaseModel):
    body: str


@router.post("/prompts", status_code=201)
def create_prompt(
    body: PromptCreateBody,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    _, kb_path = paths
    from src.db.kb import open_kb, upsert_stage_prompt
    kb_conn = open_kb(kb_path)
    prompt_id = upsert_stage_prompt(kb_conn, body.stage, body.prompt_key, body.name, body.body)
    kb_conn.close()
    return {"id": prompt_id}


@router.put("/prompts/{prompt_id}")
def update_prompt(
    prompt_id: int,
    body: PromptUpdateBody,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    _, kb_path = paths
    from src.db.kb import open_kb
    kb_conn = open_kb(kb_path)
    row = kb_conn.execute(
        "SELECT is_builtin FROM stage_prompts WHERE id=?", (prompt_id,)
    ).fetchone()
    if not row:
        kb_conn.close()
        raise HTTPException(status_code=404, detail="Prompt not found")
    if row["is_builtin"]:
        kb_conn.close()
        raise HTTPException(status_code=400, detail="Built-in prompts cannot be edited directly — create a variant instead")
    kb_conn.execute(
        "UPDATE stage_prompts SET body=? WHERE id=?", (body.body, prompt_id)
    )
    kb_conn.commit()
    kb_conn.close()
    return {"updated": prompt_id}


@router.post("/prompts/{prompt_id}/activate")
def activate_prompt(
    prompt_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    _, kb_path = paths
    from src.db.kb import open_kb, set_active_stage_prompt
    kb_conn = open_kb(kb_path)
    row = kb_conn.execute(
        "SELECT stage, prompt_key FROM stage_prompts WHERE id=?", (prompt_id,)
    ).fetchone()
    if not row:
        kb_conn.close()
        raise HTTPException(status_code=404, detail="Prompt not found")
    try:
        set_active_stage_prompt(kb_conn, row["stage"], row["prompt_key"], prompt_id)
    except ValueError as exc:
        kb_conn.close()
        raise HTTPException(status_code=404, detail=str(exc))
    kb_conn.close()
    return {"activated": prompt_id}


@router.delete("/prompts/{prompt_id}")
def delete_prompt(
    prompt_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    _, kb_path = paths
    from src.db.kb import delete_stage_prompt, open_kb
    kb_conn = open_kb(kb_path)
    try:
        delete_stage_prompt(kb_conn, prompt_id)
    except ValueError as exc:
        kb_conn.close()
        raise HTTPException(status_code=400, detail=str(exc))
    kb_conn.close()
    return {"deleted": prompt_id}
