# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
RUN pip install --no-cache-dir uv==0.12.1
WORKDIR /app
COPY pyproject.toml uv.lock ./

# Test image: dev dependencies, full source, runs pytest.
FROM base AS test
RUN uv sync --frozen
COPY . .
CMD ["pytest"]

# Runtime image (last stage = default target): production dependencies only, non-root.
FROM base AS runtime
RUN uv sync --frozen --no-dev
COPY app ./app
COPY alembic ./alembic
COPY scripts ./scripts
RUN useradd --system --uid 10001 --no-create-home baton
USER baton
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log", "--proxy-headers", "--forwarded-allow-ips=*"]
