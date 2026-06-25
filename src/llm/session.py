"""Shared LLM session classes for text and vision inference via llama_cpp."""
import gc
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ModelLoadError(Exception):
    """Raised when a llama_cpp model cannot be loaded."""


# ---------------------------------------------------------------------------
# Multimodal chat handler helpers (used only by VisionSession)
# ---------------------------------------------------------------------------

_CHAT_HANDLER_MAP = {
    "qwen2_vl":  "Qwen25VLChatHandler",
    "gemma3":    "Llava16ChatHandler",
    "moondream": "MoondreamChatHandler",
    "llava15":   "Llava15ChatHandler",
    "llava16":   "Llava16ChatHandler",
    "llava":     "Llava15ChatHandler",
}

_AUTODETECT_PATTERNS = [
    ("qwen2",     "qwen2_vl"),
    ("moondream", "moondream"),
    ("gemma",     "gemma3"),
]


def _resolve_chat_format(mmproj_path: str | None, model_path: str) -> str:
    """Infer chat format from mmproj and model filenames; falls back to 'llava'."""
    for src in (Path(mmproj_path or "").name.lower(), Path(model_path).name.lower()):
        for pattern, fmt in _AUTODETECT_PATTERNS:
            if pattern in src:
                return fmt
    return "llava"


def _make_chat_handler(mmproj_path: str, chat_format: str, model_path: str = ""):
    """Return a llama_cpp chat handler for the given mmproj file."""
    from llama_cpp import llama_chat_format as _fmt

    if not chat_format:
        chat_format = _resolve_chat_format(mmproj_path, model_path)

    handler_name = _CHAT_HANDLER_MAP.get(chat_format, "Llava15ChatHandler")
    handler_cls = getattr(_fmt, handler_name, None)
    if handler_cls is None:
        available = [x for x in dir(_fmt) if "ChatHandler" in x]
        raise ModelLoadError(
            f"Chat handler '{handler_name}' not found in installed llama_cpp. "
            f"Available handlers: {available}. "
            f"Set models.vision_chat_format in config.yaml to one of: {list(_CHAT_HANDLER_MAP)}"
        )
    return handler_cls(clip_model_path=mmproj_path, verbose=False)


# ---------------------------------------------------------------------------
# TextSession
# ---------------------------------------------------------------------------

class TextSession:
    """Context manager for text-only LLM inference via llama_cpp."""

    def __init__(
        self,
        model_path: str,
        *,
        n_gpu_layers: int = 0,
        n_ctx: int = 4096,
        verbose: bool = False,
        max_retries: int = 0,
    ):
        self._model_path = model_path
        self._n_gpu_layers = n_gpu_layers
        self._n_ctx = n_ctx
        self._verbose = verbose
        self._max_retries = max_retries
        self._llm = None

    def __enter__(self) -> "TextSession":
        try:
            from llama_cpp import Llama
            self._llm = Llama(
                model_path=self._model_path,
                n_gpu_layers=self._n_gpu_layers,
                n_ctx=self._n_ctx,
                verbose=self._verbose,
            )
        except Exception as exc:
            raise ModelLoadError(
                f"Text model failed to load: {exc}\n"
                f"Try reducing 'text_gpu_layers' in config.yaml."
            ) from exc
        return self

    def __exit__(self, *_) -> None:
        del self._llm
        gc.collect()

    def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        for attempt in range(self._max_retries + 1):
            output = self._llm.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            result = (output["choices"][0]["message"]["content"] or "").strip()
            if result:
                return result
            if attempt < self._max_retries:
                logger.debug(
                    "TextSession.generate: empty response, retry %d/%d",
                    attempt + 1, self._max_retries,
                )
            elif self._max_retries > 0:
                logger.warning(
                    "TextSession.generate: empty response after %d retries",
                    self._max_retries,
                )
        return ""


# ---------------------------------------------------------------------------
# VisionSession
# ---------------------------------------------------------------------------

class VisionSession:
    """Context manager for multimodal (vision + text) LLM inference via llama_cpp."""

    def __init__(
        self,
        model_path: str,
        *,
        mmproj_path: str | None = None,
        chat_format: str = "",
        n_gpu_layers: int = 0,
        n_ctx: int = 4096,
        verbose: bool = False,
        max_retries: int = 0,
    ):
        self._model_path = model_path
        self._mmproj_path = mmproj_path
        self._chat_format = chat_format
        self._n_gpu_layers = n_gpu_layers
        self._n_ctx = n_ctx
        self._verbose = verbose
        self._max_retries = max_retries
        self._llm = None

    def __enter__(self) -> "VisionSession":
        try:
            from llama_cpp import Llama
            load_kwargs: dict = dict(
                model_path=self._model_path,
                n_gpu_layers=self._n_gpu_layers,
                n_ctx=self._n_ctx,
                verbose=self._verbose,
            )
            if self._mmproj_path:
                load_kwargs["chat_handler"] = _make_chat_handler(
                    self._mmproj_path, self._chat_format, self._model_path
                )
            self._llm = Llama(**load_kwargs)
        except ModelLoadError:
            raise
        except Exception as exc:
            raise ModelLoadError(
                f"Vision model failed to load: {exc}\n"
                f"This is usually caused by insufficient VRAM.\n"
                f"Try reducing 'vision_gpu_layers' in config.yaml, "
                f"or set it to 0 to run on CPU (slower but works on any machine)."
            ) from exc
        return self

    def __exit__(self, *_) -> None:
        del self._llm
        gc.collect()

    def generate(
        self,
        system: str,
        user: str,
        images: list[bytes] | None = None,
        *,
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str:
        import base64

        if images:
            content: list = []
            for img_bytes in images:
                b64 = base64.b64encode(img_bytes).decode()
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
            content.append({"type": "text", "text": user})
            user_msg: dict = {"role": "user", "content": content}
        else:
            user_msg = {"role": "user", "content": user}

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append(user_msg)

        for attempt in range(self._max_retries + 1):
            output = self._llm.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            result = (output["choices"][0]["message"]["content"] or "").strip()
            if result:
                return result
            if attempt < self._max_retries:
                logger.debug(
                    "VisionSession.generate: empty response, retry %d/%d",
                    attempt + 1, self._max_retries,
                )
            elif self._max_retries > 0:
                logger.warning(
                    "VisionSession.generate: empty response after %d retries",
                    self._max_retries,
                )
        return ""
