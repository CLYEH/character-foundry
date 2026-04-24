"""Load the platform constraints YAML once per process.

Exposes the `version` string for `/v1/meta` and the full constraint lists
for the prompt reconciler (T-xxx). Cached because the file is packaged with
the image and never mutates at runtime — a restart is required to pick up
changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# `api/platform_constraints.yaml` — three dirs up from this file
# (app/core/platform_constraints.py → app/ → api/).
_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "platform_constraints.yaml"


@dataclass(frozen=True)
class PlatformConstraints:
    version: str
    updated_at: str
    base_creation: tuple[str, ...]
    alias_creation: tuple[str, ...]
    motion_creation: tuple[str, ...]


def _parse(data: dict[str, Any]) -> PlatformConstraints:
    return PlatformConstraints(
        version=str(data["version"]),
        updated_at=str(data["updated_at"]),
        base_creation=tuple(data.get("base_creation", [])),
        alias_creation=tuple(data.get("alias_creation", [])),
        motion_creation=tuple(data.get("motion_creation", [])),
    )


@lru_cache(maxsize=1)
def load_platform_constraints(path: Path | None = None) -> PlatformConstraints:
    target = path or _DEFAULT_PATH
    with open(target, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{target} is not a mapping at the top level")
    return _parse(data)
