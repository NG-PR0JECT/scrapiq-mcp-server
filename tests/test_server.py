"""End-to-end tests for the MCP server (stdio JSON-RPC)."""
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "scrapiq_mcp" / "server.py"


def call_server(messages: list[dict], env: dict | None = None) -> list[dict]:
    payload = "\n".join(json.dumps(m) for m in messages) + "\n"
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input=payload.encode(),
        capture_output=True,
        timeout=60,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"server exited {proc.returncode}: {proc.stderr.decode()}")
    out = []
    for line in proc.stdout.decode().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def test_handshake_and_list():
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    out = call_server(msgs)
    assert len(out) == 2, out
    assert out[0]["result"]["serverInfo"]["name"] == "scrapiq-mcp-server"
    tools = out[1]["result"]["tools"]
    assert tools[0]["name"] == "scrapiq_extract"
    print("PASS handshake + tools/list")


def test_extract_against_live_api():
    # Requires local Scrapiq on :8001; skip cleanly if not reachable.
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "scrapiq_extract", "arguments": {"url": "https://example.com", "format": "text"}},
        },
    ]
    try:
        out = call_server(msgs, env={"SCRAPIQ_ENDPOINT": "http://localhost:8001/v1/extract"})
    except RuntimeError:
        print("SKIP extract (no live Scrapiq on :8001)")
        return
    result = out[-1]["result"]
    text = result["content"][0]["text"]
    assert "error" not in text.lower(), text
    print("PASS extract against live API")


def test_missing_url():
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "scrapiq_extract", "arguments": {}}},
    ]
    out = call_server(msgs)
    result = out[-1]["result"]
    assert result["isError"] is True
    assert "url" in result["content"][0]["text"]
    print("PASS missing url -> isError")


def test_http_transport():
    """Streamable HTTP transport: POST /mcp works without any MCP client library."""
    import urllib.error
    import urllib.request

    port = 18002
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--http", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(1.2)

    def post(payload, path="/mcp"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    try:
        status, body = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18"}})
        assert status == 200, (status, body)
        init = json.loads(body)["result"]
        assert init["protocolVersion"] == "2025-06-18", init
        assert init["serverInfo"]["name"] == "scrapiq-mcp-server"

        # an unsupported revision must not be echoed back
        status, body = post({"jsonrpc": "2.0", "id": 8, "method": "initialize",
                             "params": {"protocolVersion": "1999-01-01"}})
        assert status == 200
        assert json.loads(body)["result"]["protocolVersion"] == "2025-06-18"

        # a notification gets 202 and an empty body, no JSON-RPC response
        status, body = post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert status == 202 and body == b"", (status, body)

        status, body = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert status == 200
        assert json.loads(body)["result"]["tools"][0]["name"] == "scrapiq_extract"

        # batch of one request + one notification-ish error
        status, body = post([{"jsonrpc": "2.0", "id": 3, "method": "ping"},
                             {"jsonrpc": "2.0", "id": 4, "method": "nope"}])
        assert status == 200
        out = json.loads(body)
        assert out[0]["result"] == {}
        assert out[1]["error"]["code"] == -32601

        # no SSE stream is offered, so GET must say so rather than hang
        req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raise AssertionError(f"GET /mcp should be 405, got {r.status}")
        except urllib.error.HTTPError as e:
            assert e.code == 405, e.code
        print("PASS http transport")
    finally:
        proc.terminate()
        proc.communicate(timeout=10)


if __name__ == "__main__":
    test_handshake_and_list()
    test_missing_url()
    test_extract_against_live_api()
    test_http_transport()
    print("All tests passed.")
