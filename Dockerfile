FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY scrapiq_mcp/ ./scrapiq_mcp/

RUN pip install --no-cache-dir .

ENV SCRAPIQ_ENDPOINT=https://scrapiq.io/v1/extract

# MCP stdio server: runs as a child process of the MCP client.
ENTRYPOINT ["scrapiq-mcp"]
