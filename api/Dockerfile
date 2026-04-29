FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY tests ./tests
# Loaded at runtime by app.core.platform_constraints — /v1/meta breaks without it.
COPY platform_constraints.yaml ./
# Editable install so `__file__`-based path resolution (the platform
# constraints YAML at the repo root, plus the AI stub PNG fixture) lands
# on the /app source tree instead of a wheel in site-packages that drops
# non-Python data files. Without this T-026's stub-pipeline E2E spec
# fails with FileNotFoundError on platform_constraints.yaml even though
# the YAML was COPY'd into /app.
RUN pip install --upgrade pip && pip install -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
