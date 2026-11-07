FROM python:3.11-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /uvx /usr/local/bin/
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY library_service ./library_service
COPY data ./data
COPY schema ./schema
RUN .venv/bin/python -m library_service.cli

FROM python:3.11-slim-bookworm
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/library_service ./library_service
COPY --from=builder /app/data ./data
COPY --from=builder /app/schema ./schema
COPY LICENSE.md ./LICENSE.md
ENV PATH="/app/.venv/bin:$PATH"
# 两个令牌必须在运行时注入；镜像不含真实凭据或预置数据库。
CMD ["python", "-m", "uvicorn", "library_service.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8012", "--workers", "1"]
