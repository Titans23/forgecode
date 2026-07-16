FROM ghcr.io/astral-sh/uv:0.11.28-python3.12-trixie-slim AS runtime

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_PROGRESS=1

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY forge ./forge
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

ENV PATH=/app/.venv/bin:$PATH

CMD [forge, --help]
