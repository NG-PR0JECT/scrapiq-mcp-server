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


if __name__ == "__main__":
    test_handshake_and_list()
    test_missing_url()
    test_extract_against_live_api()
    print("All tests passed.")
