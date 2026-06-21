"""KB health checks — shared by CLI and API."""
import importlib.util
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class HealthCheck:
    id: str
    label: str
    severity: str  # "error" | "warning" | "info"
    ok: bool
    detail: str
    fix: str = field(default="")


def run_checks(
    config,
    corpus_conn: sqlite3.Connection | None,
    kb_conn: sqlite3.Connection | None,
    kb_folder: Path,
) -> list[HealthCheck]:
    return [
        _check_exiftool(config),
        _check_ffmpeg(config),
        _check_vision_model(config),
        _check_text_model(config),
        _check_spacy_model(),
        _check_face_detection_model(config),
        _check_face_embedding_model(config),
        _check_voice_model(),
        _check_diarization_model(),
        _check_field_map(kb_folder),
        _check_sources(corpus_conn),
        _check_corpus_files(corpus_conn),
        _check_vocabulary(kb_conn),
        _check_focus(config),
        _check_unknown_fields(corpus_conn),
        _check_library_yaml(kb_folder),
        _check_exiftool_config(kb_folder),
        _check_dates_yaml(kb_folder),
        _check_derive_rules_yaml(kb_folder),
        _check_taxonomy_yaml(kb_folder),
        _check_geolocate_data(kb_folder),
        _check_privacy_zones(kb_folder),
        _check_validation_freshness(corpus_conn),
    ]


# ---------------------------------------------------------------------------
# Group A — Environment (error)
# ---------------------------------------------------------------------------

def _check_exiftool(config) -> HealthCheck:
    exe = config.exiftool
    found = shutil.which(exe) is not None or Path(exe).exists()
    return HealthCheck(
        id="exiftool",
        label="ExifTool present",
        severity="error",
        ok=found,
        detail=exe if found else f"not found: '{exe}'",
        fix="" if found else f"Place exiftool.exe at '{exe}' or add to PATH",
    )


def _check_ffmpeg(config) -> HealthCheck:
    exe = config.ffmpeg
    found = shutil.which(exe) is not None or Path(exe).exists()
    return HealthCheck(
        id="ffmpeg",
        label="ffmpeg present",
        severity="error",
        ok=found,
        detail=exe if found else f"not found: '{exe}'",
        fix="" if found else f"Place ffmpeg.exe at '{exe}' or add to PATH",
    )


# ---------------------------------------------------------------------------
# Group B — Optional tools (warning)
# ---------------------------------------------------------------------------

def _check_vision_model(config) -> HealthCheck:
    configured = config.vision_model
    if configured:
        found = Path(configured).is_file()
        return HealthCheck(
            id="vision_model",
            label="Vision model present",
            severity="warning",
            ok=found,
            detail=configured if found else f"file not found: '{configured}'",
            fix="" if found else "Update models.vision in config.yaml",
        )
    # Fall back to auto-discovery
    hits = list(Path("tools/models/vision").glob("*.gguf")) if Path("tools/models/vision").is_dir() else []
    return HealthCheck(
        id="vision_model",
        label="Vision model present",
        severity="warning",
        ok=bool(hits),
        detail=hits[0].name if hits else "no .gguf found in tools/models/vision/",
        fix="" if hits else "Place a vision GGUF in tools/models/vision/",
    )


def _check_text_model(config) -> HealthCheck:
    configured = config.text_model
    if configured:
        found = Path(configured).is_file()
        return HealthCheck(
            id="text_model",
            label="Text model present",
            severity="warning",
            ok=found,
            detail=configured if found else f"file not found: '{configured}'",
            fix="" if found else "Update models.text in config.yaml",
        )
    hits = list(Path("tools/models/text").glob("*.gguf")) if Path("tools/models/text").is_dir() else []
    return HealthCheck(
        id="text_model",
        label="Text model present",
        severity="warning",
        ok=bool(hits),
        detail=hits[0].name if hits else "no .gguf found in tools/models/text/",
        fix="" if hits else "Place a text GGUF in tools/models/text/",
    )


def _check_spacy_model() -> HealthCheck:
    found = importlib.util.find_spec("en_core_web_sm") is not None
    return HealthCheck(
        id="spacy_model",
        label="spaCy en_core_web_sm",
        severity="warning",
        ok=found,
        detail="installed" if found else "not installed",
        fix="" if found else "python -m spacy download en_core_web_sm",
    )


def _check_field_map(kb_folder: Path) -> HealthCheck:
    path = kb_folder / "reference" / "field_map.csv"
    exists = path.exists()
    return HealthCheck(
        id="field_map",
        label="reference/field_map.csv",
        severity="warning",
        ok=exists,
        detail="present" if exists else "missing — extract_fields stage will skip",
        fix="" if exists else "Run the Extract Fields stage to generate it",
    )


def _check_face_detection_model(config) -> HealthCheck:
    configured = config.face_detection_model
    if configured:
        found = Path(configured).is_file()
        return HealthCheck(
            id="face_detection_model",
            label="Face detection model present",
            severity="warning",
            ok=found,
            detail=configured if found else f"file not found: '{configured}'",
            fix="" if found else "Update models.face_detection in config.yaml",
        )
    return HealthCheck(
        id="face_detection_model",
        label="Face detection model present",
        severity="warning",
        ok=False,
        detail="not configured",
        fix="Run 'enrich face download --detection-model' or set models.face_detection in config.yaml",
    )


def _check_face_embedding_model(config) -> HealthCheck:
    configured = config.face_embedding_model
    if configured:
        found = Path(configured).is_file()
        return HealthCheck(
            id="face_embedding_model",
            label="Face embedding model present",
            severity="warning",
            ok=found,
            detail=configured if found else f"file not found: '{configured}'",
            fix="" if found else "Update models.face_embedding in config.yaml",
        )
    return HealthCheck(
        id="face_embedding_model",
        label="Face embedding model present",
        severity="warning",
        ok=False,
        detail="not configured",
        fix="Run 'enrich face download --embedding-model' or set models.face_embedding in config.yaml",
    )


def _check_voice_model() -> HealthCheck:
    found = importlib.util.find_spec("resemblyzer") is not None
    return HealthCheck(
        id="voice_model",
        label="Resemblyzer voice model",
        severity="warning",
        ok=found,
        detail="installed" if found else "not installed",
        fix="" if found else "pip install resemblyzer",
    )


def _check_diarization_model() -> HealthCheck:
    found = importlib.util.find_spec("pyannote") is not None
    return HealthCheck(
        id="diarization_model",
        label="pyannote.audio diarization",
        severity="warning",
        ok=found,
        detail="installed" if found else "not installed",
        fix="" if found else "pip install pyannote.audio",
    )


# ---------------------------------------------------------------------------
# Group C — KB state (info)
# ---------------------------------------------------------------------------

def _check_sources(corpus_conn: sqlite3.Connection | None) -> HealthCheck:
    if corpus_conn is None:
        return HealthCheck(id="sources", label="Source directories set", severity="info",
                           ok=False, detail="database unavailable")
    count = corpus_conn.execute(
        "SELECT COUNT(*) FROM sources WHERE removed_at IS NULL"
    ).fetchone()[0]
    return HealthCheck(
        id="sources",
        label="Source directories set",
        severity="info",
        ok=count > 0,
        detail=f"{count} source(s)" if count > 0 else "no sources — add a source folder to start ingesting",
    )


def _check_corpus_files(corpus_conn: sqlite3.Connection | None) -> HealthCheck:
    if corpus_conn is None:
        return HealthCheck(id="corpus_files", label="Files ingested", severity="info",
                           ok=False, detail="database unavailable")
    count = corpus_conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    return HealthCheck(
        id="corpus_files",
        label="Files ingested",
        severity="info",
        ok=count > 0,
        detail=f"{count} file(s)" if count > 0 else "no files — run Ingest",
    )


def _check_vocabulary(kb_conn: sqlite3.Connection | None) -> HealthCheck:
    if kb_conn is None:
        return HealthCheck(id="vocabulary", label="Vocabulary non-empty", severity="info",
                           ok=False, detail="database unavailable")
    count = kb_conn.execute("SELECT COUNT(*) FROM vocabulary").fetchone()[0]
    return HealthCheck(
        id="vocabulary",
        label="Vocabulary non-empty",
        severity="info",
        ok=count > 0,
        detail=f"{count} term(s)" if count > 0 else "empty — run Suggest and review candidates",
    )


def _check_focus(config) -> HealthCheck:
    has = bool(config.focus)
    return HealthCheck(
        id="focus",
        label="FOCUS string set",
        severity="info",
        ok=has,
        detail=config.focus if has else "not set — recommended for better description quality",
        fix="" if has else "Add 'focus: <your domain>' under per-KB config.yaml",
    )


def _check_unknown_fields(corpus_conn: sqlite3.Connection | None) -> HealthCheck:
    if corpus_conn is None:
        return HealthCheck(id="unknown_fields", label="Unreviewed EXIF fields", severity="info",
                           ok=True, detail="database unavailable")
    exif_count = corpus_conn.execute("SELECT COUNT(*) FROM file_exif").fetchone()[0]
    if exif_count == 0:
        return HealthCheck(
            id="unknown_fields",
            label="Unreviewed EXIF fields",
            severity="info",
            ok=True,
            detail="N/A — run Extract Metadata first",
        )
    unknown = corpus_conn.execute(
        "SELECT COUNT(DISTINCT raw_field_name) FROM file_metadata_fields "
        "WHERE canonical_name IS NULL OR canonical_name = ''"
    ).fetchone()[0]
    return HealthCheck(
        id="unknown_fields",
        label="Unreviewed EXIF fields",
        severity="info",
        ok=unknown == 0,
        detail=f"{unknown} unrecognised field(s)" if unknown > 0 else "all fields mapped",
    )


# ---------------------------------------------------------------------------
# Group D — KB scaffold files (info)
# ---------------------------------------------------------------------------

def _check_yaml_file(check_id: str, label: str, path: Path) -> HealthCheck:
    if not path.exists():
        return HealthCheck(id=check_id, label=label, severity="info", ok=False,
                           detail="missing — re-run 'enrich kb create'",
                           fix="enrich kb create --name <name>")
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
        return HealthCheck(id=check_id, label=label, severity="info", ok=True, detail="present")
    except Exception as exc:
        return HealthCheck(id=check_id, label=label, severity="info", ok=False,
                           detail=f"parse error: {exc}")


def _check_library_yaml(kb_folder: Path) -> HealthCheck:
    return _check_yaml_file("library_yaml", "library.yaml", kb_folder / "library.yaml")


def _check_exiftool_config(kb_folder: Path) -> HealthCheck:
    path = kb_folder / "reference" / "ExifTool_Config"
    exists = path.exists()
    return HealthCheck(
        id="exiftool_config",
        label="reference/ExifTool_Config",
        severity="info",
        ok=exists,
        detail="present" if exists else "missing — custom XMP namespaces (FamilyArchive) will not be read",
        fix="" if exists else "Re-run 'enrich kb create' or copy ExifTool_Config from catalogue",
    )


def _check_dates_yaml(kb_folder: Path) -> HealthCheck:
    return _check_yaml_file("dates_yaml", "reference/dates.yaml",
                            kb_folder / "reference" / "dates.yaml")


def _check_derive_rules_yaml(kb_folder: Path) -> HealthCheck:
    return _check_yaml_file("derive_rules_yaml", "reference/derive_rules.yaml",
                            kb_folder / "reference" / "derive_rules.yaml")


def _check_taxonomy_yaml(kb_folder: Path) -> HealthCheck:
    return _check_yaml_file("taxonomy_yaml", "reference/taxonomy.yaml",
                            kb_folder / "reference" / "taxonomy.yaml")


# ---------------------------------------------------------------------------
# Group E — Geo data (warning)
# ---------------------------------------------------------------------------

def _check_geolocate_data(kb_folder: Path) -> HealthCheck:
    ne_dir = kb_folder / "reference" / "geo" / "natural_earth"
    admin0 = ne_dir / "ne_10m_admin_0_countries.shp"
    admin1 = ne_dir / "ne_10m_admin_1_states_provinces.shp"
    ok = admin0.exists() and admin1.exists()
    detail = "Natural Earth admin_0 + admin_1 shapefiles present" if ok else (
        "Natural Earth shapefiles missing — geolocate stage will produce no output"
    )
    return HealthCheck(
        id="geolocate_data",
        label="Natural Earth shapefiles",
        severity="warning",
        ok=ok,
        detail=detail,
        fix="" if ok else "Run: enrich geolocate download",
    )


def _check_privacy_zones(kb_folder: Path) -> HealthCheck:
    yaml_path = kb_folder / "reference" / "privacy_zones.yaml"
    if not yaml_path.exists():
        return HealthCheck(
            id="privacy_zones",
            label="Privacy zones config",
            severity="info",
            ok=True,
            detail="No privacy_zones.yaml — GPS masking disabled",
            fix="",
        )

    missing: list[str] = []
    try:
        import yaml as _yaml
        with yaml_path.open(encoding="utf-8") as fh:
            raw = _yaml.safe_load(fh) or {}
        ref_dir = kb_folder / "reference"
        for entry in raw.get("privacy_zones") or []:
            if "file" in entry:
                fp = ref_dir / entry["file"]
                if not fp.exists():
                    missing.append(entry["file"])
    except Exception as exc:
        return HealthCheck(
            id="privacy_zones",
            label="Privacy zones config",
            severity="warning",
            ok=False,
            detail=f"Could not parse privacy_zones.yaml: {exc}",
            fix="Check YAML syntax in reference/privacy_zones.yaml",
        )

    ok = not missing
    detail = "privacy_zones.yaml loaded; all zone files present" if ok else (
        "Missing zone file(s): " + ", ".join(missing)
    )
    return HealthCheck(
        id="privacy_zones",
        label="Privacy zones config",
        severity="warning",
        ok=ok,
        detail=detail,
        fix="" if ok else "Add missing files to reference/ or remove their entries from privacy_zones.yaml",
    )


# ---------------------------------------------------------------------------
# Group F — Corpus maintenance (info / warning)
# ---------------------------------------------------------------------------

def _check_validation_freshness(corpus_conn: sqlite3.Connection | None) -> HealthCheck:
    if corpus_conn is None:
        return HealthCheck(
            id="validation_freshness",
            label="File validation",
            severity="info",
            ok=True,
            detail="database unavailable",
        )

    try:
        row = corpus_conn.execute(
            "SELECT * FROM validation_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return HealthCheck(
            id="validation_freshness",
            label="File validation",
            severity="info",
            ok=True,
            detail="No validation run recorded — consider running `enrich corpus validate`",
        )

    if row is None:
        return HealthCheck(
            id="validation_freshness",
            label="File validation",
            severity="info",
            ok=True,
            detail="No validation run recorded — consider running `enrich corpus validate`",
        )

    changed = row["changed_count"]
    missing_count = row["missing_count"]
    if changed > 0 or missing_count > 0:
        parts = []
        if changed:
            parts.append(f"{changed} changed")
        if missing_count:
            parts.append(f"{missing_count} missing")
        return HealthCheck(
            id="validation_freshness",
            label="File validation",
            severity="warning",
            ok=False,
            detail=f"Last run found issues: {', '.join(parts)}",
            fix="Investigate with `enrich corpus validate` and review the output",
        )

    return HealthCheck(
        id="validation_freshness",
        label="File validation",
        severity="info",
        ok=True,
        detail=f"Last run: {row['run_at']} — {row['files_checked']} files checked, all ok",
    )
