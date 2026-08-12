"""Scrapiq MCP server — expose Scrapiq URL→clean JSON extraction as MCP tools.

Dependency-free MCP stdio server (JSON-RPC 2.0 over stdin/stdout). Works with
Claude Desktop, Cursor, and any MCP client. Tested tools:

  - scrapiq_extract(url, format, ...)  -> extract a URL to clean text/markdown/json
"""

import json
import os
import sys
import urllib.request
import urllib.error

__version__ = "0.1.0"

# Default Scrapiq endpoint. Override with SCRAPIQ_ENDPOINT env var.
SCRAPIQ_ENDPOINT = os.environ.get("SCRAPIQ_ENDPOINT", "http://localhost:8001/v1/extract")

TOOL_NAME = "scrapiq_extract"

TOOL_SPEC = {
    "name": TOOL_NAME,
    "description": (
        "Extract a web page into clean, structured content for LLM/RAG pipelines "
        "via the Scrapiq API. Strips boilerplate, navigation, ads, and scripts. "
        "Returns title, content, links, and metadata."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to extract"},
            "format": {
                "type": "string",
                "enum": ["markdown", "text", "json"],
                "description": "Output format (default: markdown)",
            },
            "max_chars": {
                "type": "integer",
                "description": "Truncate content to N chars (default: no truncation)",
            },
        },
        "required": ["url"],
    },
}


def extract(url: str, fmt: str = "markdown", max_chars: int | None = None) -> dict:
    """Call the Scrapiq API. Returns the parsed JSON response."""
    payload = {"url": url, "format": fmt}
    if max_chars:
        payload["max_chars"] = max_chars
    req = urllib.request.Request(
        SCRAPIQ_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "scrapiq-mcp-server/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        return {"error": f"Scrapiq API error {exc.code}: {body[:500]}"}
    except Exception as exc:
        return {"error": f"Request failed: {exc}"}


def handle_tools_call(params: dict) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}
    if name != TOOL_NAME:
        return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}
    url = args.get("url")
    if not url:
        return {"content": [{"type": "text", "text": "Missing required argument: url"}], "isError": True}
    fmt = args.get("format", "markdown")
    max_chars = args.get("max_chars")
    result = extract(url, fmt, max_chars)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if len(text) > 8000:
        text = text[:8000] + "\n... (truncated by MCP server)"
    return {"content": [{"type": "text", "text": text}]}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg_id = msg.get("id")
        method = msg.get("method")

        if method == "initialize":
            result = {
                "protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "scrapiq-mcp-server", "version": __version__},
            }
        elif method == "notifications/initialized":
            continue  # no response expected
        elif method == "tools/list":
            result = {"tools": [TOOL_SPEC]}
        elif method == "tools/call":
            result = handle_tools_call(msg.get("params") or {})
        elif method == "ping":
            result = {}
        else:
            result = {"error": {"code": -32601, "message": f"Method not found: {method}"}}

        response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
