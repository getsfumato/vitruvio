FROM ghcr.io/astral-sh/uv:python3.11-trixie-slim
WORKDIR /app
COPY . .
RUN uv sync --frozen --package vitruvio-server --no-dev
ENV PATH="/app/.venv/bin:${PATH}" VITRUVIO_SERVER_HOST=0.0.0.0
# query and ingest listen here; the worker listens nowhere.
EXPOSE 8080
# One image, three roles: `docker run IMAGE` is query, `docker run IMAGE ingest`, `docker run IMAGE worker`.
# Both in exec form on purpose: a shell-form ENTRYPOINT would discard the role and silently run query.
ENTRYPOINT ["vitruvio-server"]
CMD ["query"]
