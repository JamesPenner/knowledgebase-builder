import json
import logging
import subprocess
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)


class MetaFaceRegion(TypedDict):
    name: str
    bbox_norm: tuple[float, float, float, float]  # (cx, cy, w, h) normalised 0-1
    source: str  # 'mwg-rs' | 'acdsee'


def bbox_norm_to_pixels(
    cx: float, cy: float, w: float, h: float, img_w: int, img_h: int
) -> tuple[float, float, float, float]:
    px_cx = cx * img_w
    px_cy = cy * img_h
    px_w = w * img_w
    px_h = h * img_h
    return px_cx - px_w / 2, px_cy - px_h / 2, px_cx + px_w / 2, px_cy + px_h / 2


def read_mwg_rs(exif_data: dict, img_w: int, img_h: int) -> list[MetaFaceRegion]:
    names = exif_data.get("RegionName") or []
    types = exif_data.get("RegionType") or []
    unit = exif_data.get("RegionAreaUnit", "normalized")
    xs = exif_data.get("RegionAreaX") or []
    ys = exif_data.get("RegionAreaY") or []
    ws = exif_data.get("RegionAreaW") or []
    hs = exif_data.get("RegionAreaH") or []

    if isinstance(names, str):
        names = [names]
    if isinstance(types, str):
        types = [types]
    if isinstance(xs, (int, float)):
        xs = [xs]
    if isinstance(ys, (int, float)):
        ys = [ys]
    if isinstance(ws, (int, float)):
        ws = [ws]
    if isinstance(hs, (int, float)):
        hs = [hs]

    if not names:
        return []

    if unit != "normalized":
        return []

    regions: list[MetaFaceRegion] = []
    for i, name in enumerate(names):
        if not name:
            continue
        rtype = types[i] if i < len(types) else ""
        if str(rtype).lower() not in ("face", ""):
            continue
        try:
            cx, cy, w, h = float(xs[i]), float(ys[i]), float(ws[i]), float(hs[i])
        except (IndexError, TypeError, ValueError):
            continue
        regions.append(MetaFaceRegion(name=str(name), bbox_norm=(cx, cy, w, h), source="mwg-rs"))
    return regions


def read_acdsee(exif_data: dict, img_w: int, img_h: int) -> list[MetaFaceRegion]:
    names = exif_data.get("ACDSeeRegionName") or []
    types = exif_data.get("ACDSeeRegionType") or []

    # Prefer DLY (user-saved) over ALG (auto-detected)
    dly_xs = exif_data.get("ACDSeeRegionDLYAreaX") or []
    dly_ys = exif_data.get("ACDSeeRegionDLYAreaY") or []
    dly_ws = exif_data.get("ACDSeeRegionDLYAreaW") or []
    dly_hs = exif_data.get("ACDSeeRegionDLYAreaH") or []
    alg_xs = exif_data.get("ACDSeeRegionALGAreaX") or []
    alg_ys = exif_data.get("ACDSeeRegionALGAreaY") or []
    alg_ws = exif_data.get("ACDSeeRegionALGAreaW") or []
    alg_hs = exif_data.get("ACDSeeRegionALGAreaH") or []

    if isinstance(names, str):
        names = [names]
    if isinstance(types, str):
        types = [types]
    for lst in (dly_xs, dly_ys, dly_ws, dly_hs, alg_xs, alg_ys, alg_ws, alg_hs):
        if isinstance(lst, (int, float)):
            lst = [lst]

    def _to_list(v):
        if isinstance(v, (int, float)):
            return [v]
        return v or []

    dly_xs, dly_ys, dly_ws, dly_hs = _to_list(dly_xs), _to_list(dly_ys), _to_list(dly_ws), _to_list(dly_hs)
    alg_xs, alg_ys, alg_ws, alg_hs = _to_list(alg_xs), _to_list(alg_ys), _to_list(alg_ws), _to_list(alg_hs)

    if not names:
        return []

    regions: list[MetaFaceRegion] = []
    for i, name in enumerate(names):
        if not name:
            continue
        rtype = types[i] if i < len(types) else ""
        if str(rtype).lower() not in ("face", ""):
            continue
        # Use DLY if present for this index, else ALG
        try:
            if i < len(dly_xs) and dly_xs[i] is not None:
                cx, cy, w, h = float(dly_xs[i]), float(dly_ys[i]), float(dly_ws[i]), float(dly_hs[i])
            elif i < len(alg_xs) and alg_xs[i] is not None:
                cx, cy, w, h = float(alg_xs[i]), float(alg_ys[i]), float(alg_ws[i]), float(alg_hs[i])
            else:
                continue
        except (IndexError, TypeError, ValueError):
            continue
        regions.append(MetaFaceRegion(name=str(name), bbox_norm=(cx, cy, w, h), source="acdsee"))
    return regions


READERS = [read_mwg_rs, read_acdsee]


def deduplicate_regions(regions: list[MetaFaceRegion]) -> list[MetaFaceRegion]:
    """Keep one region per name; prefer mwg-rs over acdsee."""
    seen: dict[str, MetaFaceRegion] = {}
    for region in regions:
        key = region["name"].lower().strip()
        if key not in seen:
            seen[key] = region
        elif region["source"] == "mwg-rs" and seen[key]["source"] != "mwg-rs":
            seen[key] = region
    return list(seen.values())


def _read_xmp_exif(img_path: Path, exiftool: str) -> dict:
    try:
        result = subprocess.run(
            [exiftool, "-j", "-XMP:all", str(img_path)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        import json as _json
        data = _json.loads(result.stdout)
        return data[0] if data else {}
    except Exception as exc:
        logger.debug("ExifTool XMP read failed for %s: %s", img_path, exc)
        return {}


def run_face_meta(corpus_path, kb_path, config, progress, cancel, *, scope=None) -> dict:
    """Read XMP face region metadata, embed regions, seed person centroids."""
    import time as _time
    from src.db.corpus import (
        get_files_without_meta_face_regions,
        open_corpus,
        update_pipeline_checkpoint,
        upsert_face_region,
    )
    from src.db.kb import (
        create_person_from_name,
        get_all_people_names,
        get_face_embeddings_for_person,
        get_people_with_centroids,
        open_kb,
        update_face_centroid,
        update_face_centroid_with_spread,
    )
    from src.stages.face import (
        ModelLoadError,
        compute_trimmed_centroid,
        cosine_similarity,
        detect_faces,
        embed_face,
        update_centroid,
    )

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    files_processed = 0
    regions_found = 0
    people_created = 0
    people_matched = 0
    skipped_quality = 0
    error_count = 0
    _start = _time.monotonic()

    try:
        # Pre-load name → person_id mapping
        name_to_id: dict[str, int] = get_all_people_names(kb_conn)

        # Pre-load existing centroid data
        centroid_cache: dict[int, dict] = {}
        for row in get_people_with_centroids(kb_conn):
            centroid_cache[row["id"]] = {
                "blob": bytes(row["face_centroid"]),
                "count": row["face_samples"],
            }

        # Track which persons received new embeddings this run
        persons_updated: set[int] = set()

        pending = get_files_without_meta_face_regions(corpus_conn)
        total = len(pending)
        progress.update(0, total, "Reading face metadata…")

        for i, row in enumerate(pending):
            if cancel.is_set():
                break

            img_path = Path(row["path"])
            file_id = row["id"]

            exif_data = _read_xmp_exif(img_path, config.exiftool)

            # Read image dimensions for coordinate conversion
            try:
                import io as _io
                from PIL import Image as _PILImage
                img_bytes = img_path.read_bytes()
                with _PILImage.open(_io.BytesIO(img_bytes)) as _pil:
                    img_w, img_h = _pil.size
            except Exception as exc:
                logger.warning("face_meta: could not open image %s: %s", img_path, exc)
                error_count += 1
                progress.update(i + 1, total)
                continue

            # Run all readers and deduplicate
            all_regions: list[MetaFaceRegion] = []
            for reader in READERS:
                try:
                    all_regions.extend(reader(exif_data, img_w, img_h))
                except Exception as exc:
                    logger.debug("face_meta: reader %s failed for %s: %s", reader.__name__, img_path, exc)

            regions = deduplicate_regions(all_regions)
            if not regions:
                # No metadata regions on this file — mark as processed by writing nothing
                # (the pending query will skip it next time)
                files_processed += 1
                progress.update(i + 1, total)
                continue

            file_error = False
            for region_index, region in enumerate(regions):
                cx, cy, w, h = region["bbox_norm"]
                bbox = list(bbox_norm_to_pixels(cx, cy, w, h, img_w, img_h))
                # Clamp to image bounds
                bbox[0] = max(0.0, bbox[0])
                bbox[1] = max(0.0, bbox[1])
                bbox[2] = min(float(img_w), bbox[2])
                bbox[3] = min(float(img_h), bbox[3])

                if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    logger.debug("face_meta: degenerate bbox for %s region %d", img_path, region_index)
                    error_count += 1
                    file_error = True
                    continue

                # Optional quality gate: confirm a face is actually present
                if config.face_meta_quality_gate and config.face_detection_model:
                    try:
                        import io as _io2
                        from PIL import Image as _PILImage2
                        pil = _PILImage2.open(_io2.BytesIO(img_bytes))
                        x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                        crop = pil.crop((x1, y1, x2, y2))
                        import io as _io3
                        buf = _io3.BytesIO()
                        crop.save(buf, format="JPEG")
                        crop_bytes = buf.getvalue()
                        detections = detect_faces(crop_bytes, config.face_detection_model)
                        best_score = max((d["score"] for d in detections), default=0.0)
                        if best_score < config.face_meta_quality_threshold:
                            logger.debug(
                                "face_meta: quality gate rejected %s region %d (score %.3f)",
                                img_path, region_index, best_score,
                            )
                            skipped_quality += 1
                            continue
                    except ModelLoadError:
                        raise
                    except Exception as exc:
                        logger.warning("face_meta: quality gate error for %s: %s", img_path, exc)

                # Embed the face crop
                try:
                    embedding = embed_face(img_bytes, bbox, config.face_embedding_model)
                except ModelLoadError:
                    raise
                except Exception as exc:
                    logger.warning("face_meta: embed failed for %s region %d: %s", img_path, region_index, exc)
                    error_count += 1
                    file_error = True
                    continue

                # Resolve person
                name = region["name"].strip()
                name_key = name.lower()
                if name_key not in {k.lower(): v for k, v in name_to_id.items()}:
                    # Try exact match first
                    pid = name_to_id.get(name)
                else:
                    pid = next((v for k, v in name_to_id.items() if k.lower() == name_key), None)

                if pid is None:
                    try:
                        pid = create_person_from_name(kb_conn, name)
                        name_to_id[name] = pid
                        people_created += 1
                        logger.debug("face_meta: created person '%s' (id=%d)", name, pid)
                    except Exception as exc:
                        logger.warning("face_meta: could not create person '%s': %s", name, exc)
                        error_count += 1
                        file_error = True
                        continue
                else:
                    people_matched += 1

                regions_found += 1

                # Similarity guard on centroid update
                if pid in centroid_cache:
                    old = centroid_cache[pid]
                    sim = cosine_similarity(embedding, old["blob"])
                    if sim >= config.face_meta_min_centroid_similarity:
                        new_blob, new_count = update_centroid(old["blob"], old["count"], embedding)
                        centroid_cache[pid] = {"blob": new_blob, "count": new_count}
                        update_face_centroid(kb_conn, pid, new_blob, new_count)
                        persons_updated.add(pid)
                    else:
                        logger.debug(
                            "face_meta: centroid guard skipped update for person %d (sim=%.3f)", pid, sim
                        )
                else:
                    # First embedding for this person
                    new_blob, new_count = update_centroid(None, 0, embedding)
                    centroid_cache[pid] = {"blob": new_blob, "count": new_count}
                    update_face_centroid(kb_conn, pid, new_blob, new_count)
                    persons_updated.add(pid)

                upsert_face_region(
                    corpus_conn,
                    file_id,
                    region_index,
                    json.dumps(bbox),
                    embedding,
                    pid,
                    None,
                    source="metadata",
                )

            corpus_conn.commit()
            kb_conn.commit()
            if not file_error:
                files_processed += 1
            progress.update(i + 1, total)

        # Post-run recalibration for persons that reached the threshold
        min_samples = config.face_meta_recalibrate_min_samples
        for pid in persons_updated:
            count = centroid_cache.get(pid, {}).get("count", 0)
            if count >= min_samples:
                try:
                    embeddings = get_face_embeddings_for_person(kb_conn, corpus_conn, pid)
                    result = compute_trimmed_centroid(embeddings)
                    if result is not None:
                        centroid_blob, retained, spread = result
                        update_face_centroid_with_spread(kb_conn, pid, centroid_blob, retained, spread)
                        kb_conn.commit()
                        logger.debug(
                            "face_meta: recalibrated person %d — %d/%d retained, spread=%.4f",
                            pid, retained, len(embeddings), spread,
                        )
                except Exception as exc:
                    logger.warning("face_meta: recalibration failed for person %d: %s", pid, exc)

        update_pipeline_checkpoint(
            corpus_conn, "face_meta", files_processed, 0, error_count,
            _time.monotonic() - _start,
        )
        corpus_conn.commit()
        progress.done()
    finally:
        corpus_conn.close()
        kb_conn.close()

    return {
        "files_processed": files_processed,
        "regions_found": regions_found,
        "people_created": people_created,
        "people_matched": people_matched,
        "skipped_quality": skipped_quality,
        "errors": error_count,
    }
