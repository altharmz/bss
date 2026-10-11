"""The MCP connector: bss's API as MCP tools, for any MCP client (docs/mcp.md).

A separate process (the `mcp` service, same image as the app) that reaches the app over
HTTP only - never the database. Its own state (tokens, OAuth clients) is a small SQLite
file on its own volume.
"""
