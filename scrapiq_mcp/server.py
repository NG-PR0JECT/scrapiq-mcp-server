"""Scrapiq MCP server — expose Scrapiq URL→clean JSON extraction as MCP tools.

Dependency-free MCP server (pure Python stdlib). Two transports:

  * stdio (default)  — JSON-RPC 2.0 over stdin/stdout, for Claude Desktop, Cursor,
    or any local MCP client.
  * streamable HTTP  — `scrapiq-mcp --http --port 8002`, one POST endpoint at
    `/mcp`, for remote clients and MCP registry listings.

Tools:

  - scrapiq_extract(url, format, max_chars) -> clean text/markdown/json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "0.2.0"

# Default Scrapiq endpoint. Override with SCRAPIQ_ENDPOINT env var.
SCRAPIQ_ENDPOINT = os.environ.get("SCRAPIQ_ENDPOINT", "http://localhost:8001/v1/extract")

TOOL_NAME = "scrapiq_extract"

# MCP protocol revisions this server implements. The transport semantics are the
# same for all of them (stateless JSON-RPC POST), so echoing a requested version
# is honest; anything else gets our newest. Do not add a version here unless it
# has actually been exercised against a client.
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[-1]

TOOL_SPEC = {
    "name": TOOL_NAME,
    "title": "Extract a URL to clean content",
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
        headers={"Content-Type": "application/json", "User-Agent": "scrapiq-mcp-server/0.2"},
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


def handle_message(msg: dict) -> dict | None:
    """Handle one JSON-RPC message. Returns the response, or None for notifications."""
    msg_id = msg.get("id")
    method = msg.get("method")

    if method == "initialize":
        requested = (msg.get("params") or {}).get("protocolVersion")
        agreed = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        result = {
            "protocolVersion": agreed,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "scrapiq-mcp-server", "version": __version__},
            "instructions": (
                "Scrapiq turns any URL into clean text, markdown, or JSON with no LLM in the "
                "loop. Use scrapiq_extract whenever you need a web page's readable content: "
                "it strips boilerplate/nav/ads and returns title, content, links and metadata."
            ),
        }
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [TOOL_SPEC]}}
    if method == "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": handle_tools_call(msg.get("params") or {}),
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def handle_payload(payload) -> tuple[int, object]:
    """Handle a single message or a batch. Returns (http_status, body_or_None)."""
    if isinstance(payload, list):
        responses = [r for r in (handle_message(m) for m in payload if isinstance(m, dict)) if r]
        if not responses:
            return 202, None
        return 200, responses
    if not isinstance(payload, dict):
        return 400, {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    response = handle_message(payload)
    if response is None:
        return 202, None
    return 200, response


class MCPHTTPHandler(BaseHTTPRequestHandler):
    """Streamable HTTP transport: a single POST endpoint carrying JSON-RPC.

    Stateless by design — no Mcp-Session-Id is issued and no server→client SSE
    stream is offered, so GET is answered with 405 (allowed by the spec). POST
    replies with one `application/json` body.
    """

    protocol_version = "HTTP/1.1"
    server_version = f"scrapiq-mcp/{__version__}"

    def log_message(self, format, *args):  # noqa: A002 - matches the base class signature
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Accept, Mcp-Session-Id, MCP-Protocol-Version",
        )
        self.send_header(
            "Access-Control-Expose-Headers", "Mcp-Session-Id, MCP-Protocol-Version"
        )

    def _send(self, status: int, body: object | None = None) -> None:
        if body is None:
            self.send_response(status)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):  # noqa: N802 - http.server naming
        self._send(204)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") in ("/mcp", "/health", "/healthz"):
            if self.path.rstrip("/") == "/mcp":
                # We do not offer an SSE server→client stream.
                self._send(405, {"error": "GET /mcp is not supported: this server is stateless and streams nothing. POST JSON-RPC to /mcp."})
                return
            self._send(200, {"status": "ok", "server": "scrapiq-mcp-server", "version": __version__})
            return
        self._send(404, {"error": "not found"})

    def do_DELETE(self):  # noqa: N802
        self._send(202)

    def do_POST(self):  # noqa: N802
        path = self.path.rstrip("/")
        if path not in ("/mcp", ""):
            self._send(404, {"error": "not found, POST JSON-RPC to /mcp"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 1_000_000:
            self._send(400, {"error": "empty or oversized body"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", errors="ignore"))
        except json.JSONDecodeError as exc:
            self._send(
                400,
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}},
            )
            return
        status, body = handle_payload(payload)
        self._send(status, body)


def main() -> int:
    parser = argparse.ArgumentParser(prog="scrapiq-mcp", description=__doc__)
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default=os.environ.get("SCRAPIQ_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SCRAPIQ_MCP_PORT", "8002")))
    args = parser.parse_args()

    if args.http:
        httpd = ThreadingHTTPServer((args.host, args.port), MCPHTTPHandler)
        sys.stderr.write(
            f"scrapiq-mcp-server {__version__} (streamable HTTP) on "
            f"http://{args.host}:{args.port}/mcp -> {SCRAPIQ_ENDPOINT}\n"
        )
        sys.stderr.flush()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_message(msg)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
