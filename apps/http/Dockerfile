FROM ghcr.io/astral-sh/uv:python3.11-trixie-slim AS base
WORKDIR /app
COPY . .
RUN uv sync --frozen --package vitruvio-http --no-dev
ENV PATH="/app/.venv/bin:${PATH}"

FROM base AS query
EXPOSE 8080
CMD ["uvicorn", "vitruvio.http.query:app", "--host", "0.0.0.0", "--port", "8080"]

FROM base AS ingest
EXPOSE 8080
CMD ["uvicorn", "vitruvio.http.ingest:app", "--host", "0.0.0.0", "--port", "8080"]
