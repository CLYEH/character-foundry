#!/usr/bin/env bash
# Run the three MCP / scope CI guardrails locally (T-081).
#
# Mirrors the steps in `.github/workflows/pr.yml` so you can catch a scope /
# tool-scope / allowlist violation before pushing. Runs from the `api/`
# directory (resolved relative to this script) so the scope scan and the
# `app.*` imports both resolve regardless of your current working dir.
#
# Usage:  bash scripts/lint_mcp.sh
# Exit:   non-zero if any of the three checks fails (stops at the first).
set -euo pipefail

API_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$API_DIR"

echo "== [1/3] scope coverage =="
python scripts/check_scope_coverage.py

echo "== [2/3] MCP tool scope consistency =="
python scripts/check_mcp_tool_scopes.py

echo "== [3/3] MCP client allowlist =="
python scripts/check_mcp_clients_allowlist.py

echo "All MCP guardrails passed."
