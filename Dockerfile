# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

FROM ${PYTHON_IMAGE} AS builder
ARG UV_VERSION=0.8.15
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/paperclip-mcp/.venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md paperclip-openapi.json ./
COPY src ./src
RUN python -m pip install "uv==${UV_VERSION}" \
    && uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="Paperclip MCP" \
      org.opencontainers.image.description="Schema-driven MCP gateway for the Paperclip API" \
      org.opencontainers.image.source="https://github.com/shanewiseman/paperclip-mcp" \
      org.opencontainers.image.version="0.1.0"
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid "${APP_GID}" paperclip \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
       --create-home --home-dir /home/paperclip --shell /usr/sbin/nologin paperclip \
    && chmod 0555 /home/paperclip
COPY --from=builder /opt/paperclip-mcp/.venv /opt/paperclip-mcp/.venv
ENV PATH=/opt/paperclip-mcp/.venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER paperclip
EXPOSE 8000
ENTRYPOINT ["paperclip-mcp"]
CMD ["http"]
