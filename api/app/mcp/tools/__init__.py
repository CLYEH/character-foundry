"""MCP tool implementations.

T-080 wires individual tools (currently only `hello.world`) directly from
`app/mcp/app.py`. T-081 introduces a registry pattern that moves the
wiring into per-namespace modules; until then this package is just a home
for tool code, not a discovery mechanism.
"""
