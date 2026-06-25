# Sprint KB.S1 — LLMSession: Shared Model Loading and Inference

## Scope

Introduce `src/llm/` with two independent context manager classes — `TextSession`
and `VisionSession` — that encapsulate llama_cpp model loading, VRAM release,
and retry on empty response. Wire all three LLM stages (`describe.py`,
`retag.py`, `summarize.py`) to use them. Fix the latent bug in `retag.py` where
a hardcoded llama2 chat template is used instead of the model's native chat
format.

No schema changes. No new API endpoints. No new CLI commands. No UI changes.

## Builds On

- KB.R1: Summarize stage (final LLM stage to be wired)
- All prior describe/retag stages: the shared `ModelLoadError` class and
  `deep_seek` / `deep_seek_max_iter` config fields already exist; this sprint
  wires them up for the first time

## Baseline

1116 tests passing, ruff clean.

## Deliverables

### New module

**`src/llm/__init__.py`** — empty package marker

**`src/llm/session.py`** — two independent classes:

```python
class ModelLoadError(Exception):
    """Raised when a llama_cpp model cannot be loaded."""

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
    ): ...
    def __enter__(self) -> "TextSession": ...
    def __exit__(self, *_) -> None: ...   # del self._llm + gc.collect()
    def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str: ...

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
    ): ...
    def __enter__(self) -> "VisionSession": ...
    def __exit__(self, *_) -> None: ...   # del self._llm + gc.collect()
    def generate(
        self,
        system: str,
        user: str,
        images: list[bytes] | None = None,
        *,
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str: ...
```

**Key behaviours:**

- Both classes implement the context manager protocol. `__exit__` explicitly
  `del self._llm` and calls `gc.collect()` to release VRAM reliably.
- `generate()` calls `llm.create_chat_completion()` internally (never the text
  completion API). The model's native chat template handles formatting.
- Retry fires only when `generate()` returns an empty string after stripping.
  It does not retry on exception. Retries are logged at DEBUG level; exhausted
  retries log at WARNING. When `max_retries=0`, no retry is attempted.
- `VisionSession.__init__` moves `_resolve_chat_format()`, `_make_chat_handler()`,
  `_CHAT_HANDLER_MAP`, and `_AUTODETECT_PATTERNS` from `describe.py` into
  `session.py`. These become module-level helpers used only by `VisionSession`.
- `ModelLoadError` is defined once in `src/llm/session.py`. Stage modules
  import it from there; their local `ModelLoadError` definitions are removed.

### Modified modules

**`src/stages/describe.py`**

- Remove `ModelLoadError`, `_resolve_chat_format()`, `_make_chat_handler()`,
  `_CHAT_HANDLER_MAP`, `_AUTODETECT_PATTERNS` — all move to `session.py`
- Replace model loading block in `run_describe()` with:
  ```python
  with VisionSession(
      config.vision_model,
      mmproj_path=config.vision_mmproj,
      chat_format=config.vision_chat_format,
      n_gpu_layers=config.vision_gpu_layers,
      n_ctx=32768,
      max_retries=config.deep_seek_max_iter if config.deep_seek else 0,
  ) as session:
  ```
- Replace `model.create_chat_completion(...)` calls in `_describe_image()` and
  `describe_video()`'s `_describe_frame()` with `session.generate(system, user, images=[...])`
- `run_describe_file()` (quick-describe) also uses `VisionSession` — no separate
  model loading code
- `_describe_image()` and `_describe_frame()` accept `session: VisionSession`
  instead of `model`

**`src/stages/retag.py`**

- Remove local `ModelLoadError`
- Remove `_llama.Llama(...)` loading block
- Remove `full_prompt = f"<s>[INST] <<SYS>>..."` — this hardcoded llama2
  template is the bug being fixed
- Replace with:
  ```python
  with TextSession(
      config.text_model,
      n_gpu_layers=config.text_gpu_layers,
      max_retries=config.deep_seek_max_iter if config.deep_seek else 0,
  ) as session:
  ```
- Replace `llm(full_prompt, ...)` with `session.generate(_SYSTEM_PROMPT, prompt)`
- `_SYSTEM_PROMPT` remains in `retag.py` as the stage owns its prompt content

**`src/stages/summarize.py`**

- Remove local `from llama_cpp import Llama` + `Llama(...)` loading block
- Remove `_call_llm()` helper — replaced by `session.generate()`
- Replace with:
  ```python
  with TextSession(
      config.text_model,
      n_gpu_layers=config.text_gpu_layers,
      n_ctx=config.summarize_max_transcript_tokens + 4096,
      max_retries=config.deep_seek_max_iter if config.deep_seek else 0,
  ) as session:
  ```
- `_summarize_chunks()` and the main summarize call both receive `session`
  and call `session.generate(system, user, max_tokens=...)`

**`src/stages/video.py`** (`describe_video` and `_describe_frame`)

- `_describe_frame(jpeg_bytes, model, prompt)` → `_describe_frame(jpeg_bytes, session, system, user)`
- `describe_video(...)` receives `session: VisionSession` instead of `model`
- No other changes to `video.py`

**`docs/development/ARCHITECTURE.md`**

- Add `src/llm/` to the module layout table with a brief description
- Note the one-way import rule: stages import from `src/llm/`; `src/llm/` does
  not import from stages or `src/db/`

## Acceptance Criteria

1. `python -m pytest tests/ -q` → ≥ 1128 tests passing; 0 regressions
2. `ruff check src/ tests/` → 0 errors
3. `from src.llm.session import TextSession, VisionSession, ModelLoadError` succeeds
4. `ModelLoadError` is no longer defined in `describe.py` or `retag.py`; both
   import it from `src.llm.session`
5. `retag.py` contains no llama2 template string (`[INST]`, `<<SYS>>`)
6. `retag.py` calls `session.generate()`, not `llm(...)`
7. `describe.py` contains no `_resolve_chat_format`, `_make_chat_handler`,
   `_CHAT_HANDLER_MAP`, or `_AUTODETECT_PATTERNS` definitions
8. `run_describe_file()` uses `VisionSession`, not a raw `Llama()` call
9. `TextSession.__exit__` and `VisionSession.__exit__` both call `gc.collect()`
   after deleting the model reference

## Test Targets — ~12 new tests

All in `tests/unit/test_llm_session.py`. No GPU or model file required;
llama_cpp internals are mocked.

### Chat format detection (VisionSession, non-GPU)

- `test_resolve_format_qwen2_from_model_path` — model filename contains "qwen2" → `"qwen2_vl"`
- `test_resolve_format_moondream_from_mmproj` — mmproj filename contains "moondream" → `"moondream"`
- `test_resolve_format_gemma_from_model_path` — model filename contains "gemma" → `"gemma3"`
- `test_resolve_format_fallback` — no known pattern in either path → `"llava"`

### Retry behaviour (TextSession, mocked llm)

- `test_generate_returns_on_first_nonempty` — mock returns "result" on first call;
  `generate()` returns "result"; llm called once
- `test_generate_retries_on_empty_string` — mock returns "" then "result";
  `max_retries=1`; `generate()` returns "result"; llm called twice
- `test_generate_returns_empty_after_exhausted_retries` — mock always returns "";
  `max_retries=2`; `generate()` returns ""; llm called 3 times total
- `test_generate_no_retry_on_exception` — mock raises `RuntimeError`; exception
  propagates immediately; llm called once

### VRAM release (context manager, mocked llm)

- `test_exit_deletes_model_and_collects` — after `__exit__`, confirm `gc.collect`
  was called (monkeypatch gc)

### ModelLoadError

- `test_text_session_raises_model_load_error_on_bad_path` — mock `Llama.__init__`
  to raise; `__enter__` raises `ModelLoadError`
- `test_vision_session_raises_model_load_error_on_bad_path` — same for
  `VisionSession`

### Retag prompt (unit, no DB)

- `test_retag_build_prompt_no_llama2_template` — call `_build_prompt()` and
  confirm the returned string contains no `[INST]` or `<<SYS>>` markers

## Design Notes

### Why two independent classes, not a base class

`TextSession` and `VisionSession` share `__enter__`/`__exit__` logic (~10 lines)
but have different constructors, different `generate()` signatures (images
parameter), and different internal setup (chat handler, mmproj). A shared base
class would add an inheritance hierarchy for minimal reuse. Two independent
classes are simpler to read, test, and modify independently.

### Why retry on empty string only

Exceptions from `llm` (OOM, context overflow, corrupt output) are not retriable
— repeating the call will fail again. Retry is meaningful only when the model
returns a structurally valid but empty response, which is the known failure mode
for malformed or truncated inputs. Stages catch exceptions from `session.generate()`
at the per-file loop level, as they do today.

### Why `generate()` returns raw string

JSON parsing stays in each stage's `_parse_llm_response()`. Mixing JSON
decoding into `generate()` would require conditional behaviour per caller and
make the stage's parsing functions untestable. The retry gate (empty string)
is format-agnostic and works for both plain text and JSON responses.

### retag.py bug

`retag.py` currently calls `llm(full_prompt, ...)` where `full_prompt` embeds
a hardcoded llama2 template (`<s>[INST] <<SYS>>...<</SYS>>...{prompt} [/INST]`).
This breaks silently with any non-llama2 model. Replacing with
`session.generate(system, user)` via `create_chat_completion()` lets the model's
registered chat format handle templating correctly. This is the primary
correctness fix in this sprint.

### VRAM release

`llama_cpp` does not reliably release GPU memory when a Python object goes out
of scope in all configurations. `__exit__` must explicitly `del self._llm` then
call `gc.collect()` to ensure memory is freed before the next stage's model
loads. Without this, back-to-back stage runs (describe → retag → summarize) can
exhaust VRAM.

### `run_describe_file()` included in scope

The quick-describe path (`src/cli/quick.py` → `run_describe_file()`) also loads
a vision model inline. It is included in this sprint to avoid creating a
maintenance fork of model loading logic immediately after the refactor.

### Prompt content ownership

Prompt strings (`_BASE_PROMPT`, `_SYSTEM_PROMPT`, `_build_system_prompt()`,
etc.) remain in each stage module. `src/llm/` is prompt-agnostic — it provides
the invocation layer only. A future prompt library sprint (see
`PROMPT_LIBRARY_CONCEPT.md`) will add per-KB prompt storage and loading on top
of the `generate(system, user)` signature established here.

## Out of Scope

- Prompt library / per-KB prompt management (`PROMPT_LIBRARY_CONCEPT.md`)
- `FileContext` unified text assembly (`REFACTOR_CONCEPTS.md`)
- `FrameSet` / `src/media/` layer (`FRAMESET_CONCEPT.md`)
- Stage loop runner (`REFACTOR_CONCEPTS.md`)
- Any schema migration
- Any API or UI change
