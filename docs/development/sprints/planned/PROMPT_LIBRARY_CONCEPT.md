# Concept: Per-KB Prompt Library

## Summary

Allow users to customise and select the system prompts used by LLM stages
(Describe, Retag, Summarize) on a per-KB basis. Prompts are stored in
`knowledge.db`, editable through the web UI, and loaded by each stage at
runtime. Built-in defaults remain in code; user-defined prompts override them
by name.

## Relationship to LLMSession

`TextSession.generate(system, user)` and `VisionSession.generate(system, user, images)`
accept prompts as parameters — the session is prompt-agnostic. The prompt
library is the layer that supplies those strings. Stages currently pass
module-level constants (`_BASE_PROMPT`, `_SYSTEM_PROMPT`) as the `system`
argument; after this sprint they would call `load_stage_prompt(kb_conn, stage,
default=_BASE_PROMPT)` instead.

The `LLMSession` sprint (KB.S1) establishes the `generate(system, user)`
signature specifically to leave a clean seam for this pattern. Do not mix
prompt management into KB.S1.

## Problem Statement

Every LLM stage has a module-level prompt constant that is not user-visible or
configurable without editing source code:

| Stage | Constant | Location |
|---|---|---|
| Describe (image) | `_BASE_PROMPT` | `describe.py` |
| Describe (video frame) | `_FRAME_PROMPT` | `video.py` |
| Describe (video aggregate) | assembled inline | `video.py:_aggregate_descriptions` |
| Retag | `_SYSTEM_PROMPT` | `retag.py` |
| Summarize | assembled by `_build_system_prompt()` | `summarize.py` |

A user with a specialised corpus (legal documents, archival photography,
technical training footage) often needs a different instruction set than the
generic defaults. Currently they have no way to provide one.

## Proposed Design

### `stage_prompts` table (knowledge.db)

```sql
CREATE TABLE IF NOT EXISTS stage_prompts (
    id          INTEGER PRIMARY KEY,
    stage       TEXT NOT NULL,        -- 'describe' | 'retag' | 'summarize'
    prompt_key  TEXT NOT NULL,        -- 'system' | 'frame' | 'aggregate'
    name        TEXT NOT NULL,        -- user-facing label, e.g. 'Archival Photography'
    body        TEXT NOT NULL,        -- prompt text
    is_active   INTEGER NOT NULL DEFAULT 0,  -- 1 = this prompt is selected for the stage
    is_builtin  INTEGER NOT NULL DEFAULT 0,  -- 1 = shipped with KB Builder, not user-created
    created_at  TEXT,
    UNIQUE (stage, prompt_key, name)
);
```

At KB creation, the table is populated with the current built-in prompts
(`is_builtin=1`, `is_active=1`). This gives users a readable baseline and a
starting point for customisation.

### Stage loading

```python
def load_stage_prompt(kb_conn, stage: str, prompt_key: str, default: str) -> str:
    """Return the active prompt body for (stage, prompt_key), or default if none."""
```

Each LLM stage calls `load_stage_prompt()` once before the per-file loop and
passes the result to `session.generate(system=..., user=...)`.

### Web UI

A **Prompts** section under the KB Settings page (or its own nav entry):

- List all prompts grouped by stage
- Show which is active; allow switching active prompt with one click
- Edit prompt body in a `<textarea>`; save writes to `stage_prompts`
- Create new prompt (copies body from active as starting point)
- Delete user-created prompts (built-ins cannot be deleted)
- Reset to built-in defaults

### Config.yaml override (simpler alternative for power users)

```yaml
prompts:
  describe_system: "Custom describe instruction here."
  retag_system: "Custom retag instruction here."
```

Config-level overrides take precedence over the DB active prompt. This allows
scripted or version-controlled prompt management without using the UI.

## Prompt Keys Per Stage

| Stage | `prompt_key` | Description |
|---|---|---|
| Describe | `system` | Base image/video description instruction |
| Describe | `frame` | Per-frame instruction for video VLM calls |
| Describe | `aggregate` | Instruction for aggregating frame descriptions |
| Retag | `system` | System instruction for tagging + refinement |
| Summarize | `system` | System instruction for summary generation |

## Payoff

- Corpus-specific instruction tuning without touching source code
- Prompt variants are versioned and named (no mystery about which prompt
  produced which result)
- Built-in defaults remain in code; the DB layer is purely additive
- Opens the door to prompt-level A/B testing across re-runs

## Prerequisites

- KB.S1 (LLMSession): `generate(system, user)` signature must be in place
- All LLM stages must use `session.generate()` rather than calling
  `llm.create_chat_completion()` directly

## When to Schedule

After KB.S1 is complete and the prompt constants are cleanly separated from the
model invocation layer. Prompts are currently embedded in stage modules; the
`LLMSession` refactor makes them explicit parameters, at which point the prompt
library can slot in without touching the session or model loading code.
