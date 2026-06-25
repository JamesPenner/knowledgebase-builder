import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_GLOBAL_ONLY = {"host", "port", "exiftool", "ffmpeg", "ffprobe", "whisper_cli"}
_PER_KB_ONLY = {"sources", "focus", "exiftool_config"}


@dataclass(frozen=True)
class Config:
    # server (global-only)
    host: str = "127.0.0.1"
    port: int = 7700
    # tools (global-only)
    exiftool: str = "tools/exiftool.exe"
    ffmpeg: str = "tools/ffmpeg.exe"
    ffprobe: str = "tools/ffprobe.exe"
    whisper_cli: str = ""  # optional: path to whisper-cli.exe (Vulkan build); empty = use pywhispercpp
    # workers
    workers: int = 4
    # thresholds
    npmi_min_weight: float = 0.1
    suggest_min_files: int = 3
    phash_threshold: int = 10
    describe_frames: int = 9
    describe_min_frame_brightness: float = 30.0
    describe_min_frame_sharpness: float = 0.0
    scene_threshold: float = 0.4
    deep_seek: bool = True
    deep_seek_max_iter: int = 2
    # write-back
    include_synonyms: bool = False
    confirm_above: int = 200
    writeback_fields: tuple = ("IPTC:Keywords", "XMP:Subject", "XMP:Description")
    # models
    vision_model: str = ""
    vision_mmproj: str = ""
    vision_chat_format: str = ""   # qwen2_vl|gemma3|moondream|llava15|llava16|llava; auto-detected if empty
    vision_gpu_layers: int = -1
    text_model: str = ""
    text_gpu_layers: int = -1
    audio_model: str = ""
    audio_gpu_layers: int = -1
    aesthetic_nima: str = ""
    aesthetic_clip: str = ""
    face_detection_model: str = ""
    face_embedding_model: str = ""
    voice_model: str = "resemblyzer"
    diarization_model: str = "pyannote/speaker-diarization-3.1"
    # thresholds (face/voice)
    face_similarity_threshold: float = 0.55
    voice_similarity_threshold: float = 0.75
    voice_diarization_min_segment_ms: int = 500
    near_duplicate_hamming_threshold: int = 10
    gps_cluster_eps_km: float = 1.0
    gps_cluster_min_samples: int = 3
    # write-back (face)
    unknown_face_clusters: bool = False
    export_biometric: bool = False
    # audio preparation (Stage 3b / voice / diarize)
    vad_silence_threshold: float = -50.0
    audio_profile: str = "default"
    visual_profile: str = "default"
    # summarize (Stage 3c)
    summarize_target_words: int = 150
    summarize_max_transcript_tokens: int = 18000
    summarize_output_field: str = ""
    # debug
    debug_frames_dir: str = ""
    # per-KB only
    sources: tuple = ()
    focus: str = ""
    exiftool_config: str = ""


def _load_yaml(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("Could not load config %s: %s", path, exc)
        return {}


def _typed(value: Any, expected_type: type, fallback: Any, key: str) -> Any:
    if not isinstance(value, expected_type):
        logger.warning("Config key %r: expected %s, got %s — using fallback", key, expected_type.__name__, type(value).__name__)
        return fallback
    return value


def _extract_global(raw: dict) -> dict:
    fields: dict = {}
    defaults = Config()

    server = raw.get("server", {}) or {}
    fields["host"] = _typed(server.get("host", defaults.host), str, defaults.host, "server.host")
    fields["port"] = _typed(server.get("port", defaults.port), int, defaults.port, "server.port")

    tools = raw.get("tools", {}) or {}
    fields["exiftool"] = _typed(tools.get("exiftool", defaults.exiftool), str, defaults.exiftool, "tools.exiftool")
    fields["ffmpeg"] = _typed(tools.get("ffmpeg", defaults.ffmpeg), str, defaults.ffmpeg, "tools.ffmpeg")
    fields["ffprobe"] = _typed(tools.get("ffprobe", defaults.ffprobe), str, defaults.ffprobe, "tools.ffprobe")
    fields["whisper_cli"] = _typed(tools.get("whisper_cli", defaults.whisper_cli), str, defaults.whisper_cli, "tools.whisper_cli")

    workers = raw.get("workers", {}) or {}
    fields["workers"] = _typed(workers.get("default", defaults.workers), int, defaults.workers, "workers.default")

    fields.update(_extract_overridable(raw, defaults))
    return fields


def _extract_overridable(raw: dict, defaults: Config) -> dict:
    """Extract all overridable keys from raw, filling absent ones from defaults."""
    fields: dict = {}

    models = raw.get("models", {}) or {}
    fields["vision_model"] = _typed(models.get("vision", defaults.vision_model), str, defaults.vision_model, "models.vision")
    fields["vision_mmproj"] = _typed(models.get("vision_mmproj", defaults.vision_mmproj), str, defaults.vision_mmproj, "models.vision_mmproj")
    fields["vision_chat_format"] = _typed(models.get("vision_chat_format", defaults.vision_chat_format), str, defaults.vision_chat_format, "models.vision_chat_format")
    fields["vision_gpu_layers"] = _typed(models.get("vision_gpu_layers", defaults.vision_gpu_layers), int, defaults.vision_gpu_layers, "models.vision_gpu_layers")
    fields["text_model"] = _typed(models.get("text", defaults.text_model), str, defaults.text_model, "models.text")
    fields["text_gpu_layers"] = _typed(models.get("text_gpu_layers", defaults.text_gpu_layers), int, defaults.text_gpu_layers, "models.text_gpu_layers")
    fields["audio_model"] = _typed(models.get("audio", defaults.audio_model), str, defaults.audio_model, "models.audio")
    fields["audio_gpu_layers"] = _typed(models.get("audio_gpu_layers", defaults.audio_gpu_layers), int, defaults.audio_gpu_layers, "models.audio_gpu_layers")
    fields["aesthetic_nima"] = _typed(models.get("aesthetic_nima", defaults.aesthetic_nima), str, defaults.aesthetic_nima, "models.aesthetic_nima")
    fields["aesthetic_clip"] = _typed(models.get("aesthetic_clip", defaults.aesthetic_clip), str, defaults.aesthetic_clip, "models.aesthetic_clip")
    fields["face_detection_model"] = _typed(models.get("face_detection", defaults.face_detection_model), str, defaults.face_detection_model, "models.face_detection")
    fields["face_embedding_model"] = _typed(models.get("face_embedding", defaults.face_embedding_model), str, defaults.face_embedding_model, "models.face_embedding")
    fields["voice_model"] = _typed(models.get("voice", defaults.voice_model), str, defaults.voice_model, "models.voice")
    fields["diarization_model"] = _typed(models.get("diarization", defaults.diarization_model), str, defaults.diarization_model, "models.diarization")

    thresholds = raw.get("thresholds", {}) or {}
    fields["npmi_min_weight"] = _typed(thresholds.get("npmi_min_weight", defaults.npmi_min_weight), float, defaults.npmi_min_weight, "thresholds.npmi_min_weight")
    fields["suggest_min_files"] = _typed(thresholds.get("suggest_min_files", defaults.suggest_min_files), int, defaults.suggest_min_files, "thresholds.suggest_min_files")
    fields["phash_threshold"] = _typed(thresholds.get("phash_threshold", defaults.phash_threshold), int, defaults.phash_threshold, "thresholds.phash_threshold")
    fields["describe_frames"] = _typed(thresholds.get("describe_frames", defaults.describe_frames), int, defaults.describe_frames, "thresholds.describe_frames")
    fields["describe_min_frame_brightness"] = _typed(thresholds.get("describe_min_frame_brightness", defaults.describe_min_frame_brightness), float, defaults.describe_min_frame_brightness, "thresholds.describe_min_frame_brightness")
    fields["describe_min_frame_sharpness"] = _typed(thresholds.get("describe_min_frame_sharpness", defaults.describe_min_frame_sharpness), float, defaults.describe_min_frame_sharpness, "thresholds.describe_min_frame_sharpness")
    fields["scene_threshold"] = _typed(thresholds.get("scene_threshold", defaults.scene_threshold), float, defaults.scene_threshold, "thresholds.scene_threshold")
    fields["deep_seek"] = _typed(thresholds.get("deep_seek", defaults.deep_seek), bool, defaults.deep_seek, "thresholds.deep_seek")
    fields["deep_seek_max_iter"] = _typed(thresholds.get("deep_seek_max_iter", defaults.deep_seek_max_iter), int, defaults.deep_seek_max_iter, "thresholds.deep_seek_max_iter")
    fields["face_similarity_threshold"] = _typed(thresholds.get("face_similarity_threshold", defaults.face_similarity_threshold), float, defaults.face_similarity_threshold, "thresholds.face_similarity_threshold")
    fields["voice_similarity_threshold"] = _typed(thresholds.get("voice_similarity_threshold", defaults.voice_similarity_threshold), float, defaults.voice_similarity_threshold, "thresholds.voice_similarity_threshold")
    fields["voice_diarization_min_segment_ms"] = _typed(thresholds.get("voice_diarization_min_segment_ms", defaults.voice_diarization_min_segment_ms), int, defaults.voice_diarization_min_segment_ms, "thresholds.voice_diarization_min_segment_ms")
    fields["near_duplicate_hamming_threshold"] = _typed(thresholds.get("near_duplicate_hamming_threshold", defaults.near_duplicate_hamming_threshold), int, defaults.near_duplicate_hamming_threshold, "thresholds.near_duplicate_hamming_threshold")
    fields["gps_cluster_eps_km"] = _typed(thresholds.get("gps_cluster_eps_km", defaults.gps_cluster_eps_km), float, defaults.gps_cluster_eps_km, "thresholds.gps_cluster_eps_km")
    fields["gps_cluster_min_samples"] = _typed(thresholds.get("gps_cluster_min_samples", defaults.gps_cluster_min_samples), int, defaults.gps_cluster_min_samples, "thresholds.gps_cluster_min_samples")
    fields["vad_silence_threshold"] = _typed(thresholds.get("vad_silence_threshold", defaults.vad_silence_threshold), float, defaults.vad_silence_threshold, "thresholds.vad_silence_threshold")
    fields["audio_profile"] = _typed(thresholds.get("audio_profile", defaults.audio_profile), str, defaults.audio_profile, "thresholds.audio_profile")
    fields["visual_profile"] = _typed(thresholds.get("visual_profile", defaults.visual_profile), str, defaults.visual_profile, "thresholds.visual_profile")

    summarize = raw.get("summarize", {}) or {}
    fields["summarize_target_words"] = _typed(summarize.get("target_words", defaults.summarize_target_words), int, defaults.summarize_target_words, "summarize.target_words")
    fields["summarize_max_transcript_tokens"] = _typed(summarize.get("max_transcript_tokens", defaults.summarize_max_transcript_tokens), int, defaults.summarize_max_transcript_tokens, "summarize.max_transcript_tokens")
    fields["summarize_output_field"] = _typed(summarize.get("output_field", defaults.summarize_output_field), str, defaults.summarize_output_field, "summarize.output_field")

    write_back = raw.get("write_back", {}) or {}
    fields["include_synonyms"] = _typed(write_back.get("include_synonyms", defaults.include_synonyms), bool, defaults.include_synonyms, "write_back.include_synonyms")
    fields["confirm_above"] = _typed(write_back.get("confirm_above", defaults.confirm_above), int, defaults.confirm_above, "write_back.confirm_above")
    wb_fields = write_back.get("fields")
    if wb_fields is not None:
        fields["writeback_fields"] = tuple(wb_fields) if isinstance(wb_fields, list) else defaults.writeback_fields
    fields["unknown_face_clusters"] = _typed(write_back.get("unknown_face_clusters", defaults.unknown_face_clusters), bool, defaults.unknown_face_clusters, "write_back.unknown_face_clusters")
    fields["export_biometric"] = _typed(write_back.get("export_biometric", defaults.export_biometric), bool, defaults.export_biometric, "write_back.export_biometric")

    debug = raw.get("debug", {}) or {}
    fields["debug_frames_dir"] = _typed(debug.get("frames_dir", defaults.debug_frames_dir), str, defaults.debug_frames_dir, "debug.frames_dir")

    workers = raw.get("workers", {}) or {}
    if "count" in workers:
        fields["workers"] = _typed(workers["count"], int, fields.get("workers", defaults.workers), "workers.count")

    return fields


def _extract_per_kb(raw: dict, global_fields: dict) -> dict:
    """Extract only the keys explicitly present in the per-KB YAML.

    Uses global_fields values (not Config() defaults) as fallbacks for type
    validation so that an invalid per-KB value falls back to the global value.
    """
    fields: dict = {}

    def _fallback(key: str) -> Any:
        return global_fields.get(key, getattr(Config(), key))

    models = raw.get("models", {}) or {}
    if "vision" in models:
        fields["vision_model"] = _typed(models["vision"], str, _fallback("vision_model"), "models.vision")
    if "vision_mmproj" in models:
        fields["vision_mmproj"] = _typed(models["vision_mmproj"], str, _fallback("vision_mmproj"), "models.vision_mmproj")
    if "vision_chat_format" in models:
        fields["vision_chat_format"] = _typed(models["vision_chat_format"], str, _fallback("vision_chat_format"), "models.vision_chat_format")
    if "vision_gpu_layers" in models:
        fields["vision_gpu_layers"] = _typed(models["vision_gpu_layers"], int, _fallback("vision_gpu_layers"), "models.vision_gpu_layers")
    if "text" in models:
        fields["text_model"] = _typed(models["text"], str, _fallback("text_model"), "models.text")
    if "text_gpu_layers" in models:
        fields["text_gpu_layers"] = _typed(models["text_gpu_layers"], int, _fallback("text_gpu_layers"), "models.text_gpu_layers")
    if "audio" in models:
        fields["audio_model"] = _typed(models["audio"], str, _fallback("audio_model"), "models.audio")
    if "audio_gpu_layers" in models:
        fields["audio_gpu_layers"] = _typed(models["audio_gpu_layers"], int, _fallback("audio_gpu_layers"), "models.audio_gpu_layers")
    if "aesthetic_nima" in models:
        fields["aesthetic_nima"] = _typed(models["aesthetic_nima"], str, _fallback("aesthetic_nima"), "models.aesthetic_nima")
    if "aesthetic_clip" in models:
        fields["aesthetic_clip"] = _typed(models["aesthetic_clip"], str, _fallback("aesthetic_clip"), "models.aesthetic_clip")
    if "face_detection" in models:
        fields["face_detection_model"] = _typed(models["face_detection"], str, _fallback("face_detection_model"), "models.face_detection")
    if "face_embedding" in models:
        fields["face_embedding_model"] = _typed(models["face_embedding"], str, _fallback("face_embedding_model"), "models.face_embedding")
    if "voice" in models:
        fields["voice_model"] = _typed(models["voice"], str, _fallback("voice_model"), "models.voice")
    if "diarization" in models:
        fields["diarization_model"] = _typed(models["diarization"], str, _fallback("diarization_model"), "models.diarization")

    thresholds = raw.get("thresholds", {}) or {}
    _threshold_map = {
        "npmi_min_weight": ("npmi_min_weight", float),
        "suggest_min_files": ("suggest_min_files", int),
        "phash_threshold": ("phash_threshold", int),
        "describe_frames": ("describe_frames", int),
        "scene_threshold": ("scene_threshold", float),
        "deep_seek": ("deep_seek", bool),
        "deep_seek_max_iter": ("deep_seek_max_iter", int),
        "face_similarity_threshold": ("face_similarity_threshold", float),
        "voice_similarity_threshold": ("voice_similarity_threshold", float),
        "voice_diarization_min_segment_ms": ("voice_diarization_min_segment_ms", int),
        "gps_cluster_eps_km": ("gps_cluster_eps_km", float),
        "gps_cluster_min_samples": ("gps_cluster_min_samples", int),
        "vad_silence_threshold": ("vad_silence_threshold", float),
        "audio_profile": ("audio_profile", str),
        "visual_profile": ("visual_profile", str),
    }
    for yaml_key, (field_name, expected) in _threshold_map.items():
        if yaml_key in thresholds:
            fields[field_name] = _typed(thresholds[yaml_key], expected, _fallback(field_name), f"thresholds.{yaml_key}")

    summarize = raw.get("summarize", {}) or {}
    if "target_words" in summarize:
        fields["summarize_target_words"] = _typed(summarize["target_words"], int, _fallback("summarize_target_words"), "summarize.target_words")
    if "max_transcript_tokens" in summarize:
        fields["summarize_max_transcript_tokens"] = _typed(summarize["max_transcript_tokens"], int, _fallback("summarize_max_transcript_tokens"), "summarize.max_transcript_tokens")
    if "output_field" in summarize:
        fields["summarize_output_field"] = _typed(summarize["output_field"], str, _fallback("summarize_output_field"), "summarize.output_field")

    write_back = raw.get("write_back", {}) or {}
    if "include_synonyms" in write_back:
        fields["include_synonyms"] = _typed(write_back["include_synonyms"], bool, _fallback("include_synonyms"), "write_back.include_synonyms")
    if "confirm_above" in write_back:
        fields["confirm_above"] = _typed(write_back["confirm_above"], int, _fallback("confirm_above"), "write_back.confirm_above")
    if "fields" in write_back:
        wb_fields = write_back["fields"]
        fields["writeback_fields"] = tuple(wb_fields) if isinstance(wb_fields, list) else _fallback("writeback_fields")
    if "unknown_face_clusters" in write_back:
        fields["unknown_face_clusters"] = _typed(write_back["unknown_face_clusters"], bool, _fallback("unknown_face_clusters"), "write_back.unknown_face_clusters")
    if "export_biometric" in write_back:
        fields["export_biometric"] = _typed(write_back["export_biometric"], bool, _fallback("export_biometric"), "write_back.export_biometric")

    debug = raw.get("debug", {}) or {}
    if "frames_dir" in debug:
        fields["debug_frames_dir"] = _typed(debug["frames_dir"], str, _fallback("debug_frames_dir"), "debug.frames_dir")

    workers = raw.get("workers", {}) or {}
    if "count" in workers:
        fields["workers"] = _typed(workers["count"], int, _fallback("workers"), "workers.count")

    if "sources" in raw:
        src = raw["sources"]
        fields["sources"] = tuple(src) if isinstance(src, list) else ()
    if "focus" in raw:
        fields["focus"] = _typed(raw["focus"], str, "", "focus")
    if "exiftool_config" in raw:
        fields["exiftool_config"] = _typed(raw["exiftool_config"], str, "", "exiftool_config")

    return fields


def load_config(global_path: Path | None, kb_path: Path | None = None) -> Config:
    global_raw = _load_yaml(global_path)
    kb_raw = _load_yaml(kb_path)

    global_fields = _extract_global(global_raw)
    global_fields.update(_extract_per_kb(kb_raw, global_fields))

    # Resolve relative tool paths to absolute so subprocess.run works on Windows
    # (CreateProcess does not search relative paths the way a shell does).
    for _key in ("exiftool", "ffmpeg", "ffprobe"):
        if _key in global_fields:
            _p = Path(global_fields[_key])
            if not _p.is_absolute():
                global_fields[_key] = str(_p.resolve())
    if global_fields.get("whisper_cli"):
        _p = Path(global_fields["whisper_cli"])
        if not _p.is_absolute():
            global_fields["whisper_cli"] = str(_p.resolve())

    return Config(**global_fields)
