FROM python:3.11-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /uvx /usr/local/bin/

WORKDIR /opt/services/wenxianlu-library

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY manage.py ./manage.py
COPY server ./server
COPY cultivation ./cultivation
COPY data/cultivation ./data/cultivation
RUN .venv/bin/python manage.py migrate --noinput
RUN .venv/bin/python manage.py import_library data/cultivation/starter-v0.1.json
RUN .venv/bin/python manage.py collectstatic --noinput
RUN .venv/bin/python -m gunicorn --version

FROM python:3.11-slim-bookworm

WORKDIR /opt/services/wenxianlu-library

COPY --from=builder /opt/services/wenxianlu-library/.venv ./.venv
COPY --from=builder /opt/services/wenxianlu-library/server ./server
COPY --from=builder /opt/services/wenxianlu-library/cultivation ./cultivation
COPY --from=builder /opt/services/wenxianlu-library/data/cultivation ./data/cultivation
COPY --from=builder /opt/services/wenxianlu-library/var ./var
COPY --from=builder /opt/services/wenxianlu-library/manage.py ./manage.py
COPY LICENSE.md ./LICENSE.md

ENV PATH="/opt/services/wenxianlu-library/.venv/bin:$PATH" \
    WEB_CONCURRENCY=3 \
    GUNICORN_TIMEOUT=120

RUN python -m gunicorn --version

CMD ["sh", "-c", "python -m gunicorn -b :8888 -w ${WEB_CONCURRENCY} --timeout ${GUNICORN_TIMEOUT} server.wsgi:application"]
