FROM ghcr.io/astral-sh/uv:0.11.28-python3.12-trixie-slim AS dependencies

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_PROGRESS=1

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

FROM dependencies AS public-test

COPY src ./src
COPY tests/public ./tests/public
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

CMD [uv, run, --frozen, pytest, tests/public]

FROM public-test AS evaluation

COPY tests/hidden ./tests/hidden

CMD [uv, run, --frozen, pytest, tests/public, tests/hidden]
