"""H2 Protocol — Taiyi as an MCP server.

Exposes Taiyi's governed capabilities to MCP clients (Claude Code / Cursor / …).
"""

from taiyi.mcp.server import MCPServer, PROTOCOL_VERSION
from taiyi.mcp.tools import ToolDef, build_tools

__all__ = ["MCPServer", "PROTOCOL_VERSION", "ToolDef", "build_tools"]
