"""Make `api/scripts/` importable so tests can exercise the guardrail scripts.

The CI guardrail scripts (`check_scope_coverage.py` etc.) live in `api/scripts/`,
which is NOT part of the installed `app` package. When run as
`python scripts/check_*.py`, Python puts the script's own directory on
`sys.path[0]` automatically — which is how each script's `import _route_scan`
resolves. These tests import the scripts' functions directly, so we replicate
that path entry here.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
