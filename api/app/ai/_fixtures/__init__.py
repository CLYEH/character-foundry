"""Bundled binary fixtures for the AI stub client.

`sample_base.png` is loaded by `app.ai.stub.StubAIClient` and shipped with
the package so dev / CI / E2E never touch the real provider. Re-generate
via `scripts/generate_stub_png.py` if the dimensions ever change.
"""

from __future__ import annotations
