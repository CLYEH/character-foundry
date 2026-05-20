"""Pydantic input/output schemas for the MCP tool registry (T-088).

These models are carried as `MCPTool.input_schema` / `output_schema`
metadata so the registry is self-describing without re-reading handler
signatures (see `app/mcp/registry.py`). FastMCP itself still derives the
wire schema from each handler's signature; these models exist for the
declarative registry view + future schema-export tooling.

Output schemas reuse the existing `app.schemas.*` DTOs where one already
matches the wrapped REST endpoint's response (`TaskResponse`,
`PromptPreviewResponse`, ...) so the MCP surface can't drift from `/v1/*`.
Input schemas live here because the tools' inputs are MCP-specific
(e.g. `task.list` exposes a `TaskStatus` literal filter rather than the
route's free-form query string).
"""
