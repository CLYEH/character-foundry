"""Prompt-reconciliation package (T-015).

`app.prompt` owns the user-input → final-AI-prompt pipeline:

  - `constraints` — per-mode platform constraint surface (with alias inheritance flattened)
  - `menu_fragments` — menu key/value → English fragment mapping (Phase 1 minimal)
  - `errors` — `PROMPT_CONFLICT` / `PROMPT_RECONCILE_FAILED` factories
  - `reconciler` — `PromptReconciler.reconcile` / `.preview` orchestration

The LLM hop lives in `app.ai.reconciler_client` so it can share the
GptImage2 retry / circuit / error infrastructure.
"""

from __future__ import annotations
