# Scrapiq MCP Server

> MCP server for [Scrapiq](https://github.com/NG-PR0JECT/scrapiq) — turn any URL into clean text, markdown, or JSON for LLM/RAG pipelines, directly from your MCP client.

[Scrapiq](https://github.com/NG-PR0JECT/scrapiq) is a lightweight open-source HTTP API that fetches a web page and returns clean content — boilerplate stripped. This server exposes it as a Model Context Protocol (MCP) tool so Claude Desktop, Cursor, and any MCP client can extract clean web content with one call.

Dependency-free: pure Python stdlib, JSON-RPC 2.0 over stdio. No pip packages, no node_modules.

## Install

Not on PyPI yet, so install straight from this repo:

```bash
# run it without installing anything (run against `main`, 2026-09-17)
uvx --from git+https://github.com/NG-PR0JECT/scrapiq-mcp-server scrapiq-mcp

# or install it into an isolated environment
pipx install git+https://github.com/NG-PR0JECT/scrapiq-mcp-server
```

Requires a running Scrapiq instance (see [Scrapiq README](https://github.com/NG-PR0JECT/scrapiq#quick-start-self-hosted) — `git clone`, `pip install -e ".[dev]"`, then `scrapiq`). Point the server at it:

```bash
SCRAPIQ_ENDPOINT=http://localhost:8001/v1/extract scrapiq-mcp
```

## Usage with Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "scrapiq": {
      "command": "scrapiq-mcp",
      "env": { "SCRAPIQ_ENDPOINT": "http://localhost:8001/v1/extract" }
    }
  }
}
```

## Tool

### `scrapiq_extract`

Extract a web page into clean structured content.

**Arguments:**
- `url` (string, required) — the URL to extract
- `format` (string, optional) — `"markdown"` (default) | `"text"` | `"json"`
- `max_chars` (integer, optional) — truncate content to N chars

**Example:**
```
scrapiq_extract(url="https://en.wikipedia.org/wiki/Retrieval-augmented_generation", format="markdown")
```

Returns title, content, links, and metadata — no ads, no nav, no scripts.

## Test the server

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"scrapiq_extract","arguments":{"url":"https://example.com","format":"text"}}}' \
  | scrapiq-mcp
```

## License

MIT
