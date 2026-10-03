# syntax=docker/dockerfile:1.7

# ---------- base ----------
# Pinned by digest (the multi-arch index): a moved tag must never rebuild the image by
# surprise. Bump deliberately - see CLAUDE.md, "Base image".
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app

# ---------- builder ----------
FROM base AS builder
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---------- dev ----------
# Used only by compose.override.yaml. Adds test/lint tooling; source is bind-mounted.
FROM base AS dev
COPY --from=builder /opt/venv /opt/venv
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
# uid/gid 1000 matches the default WSL user, so bind-mounted files stay writable
RUN useradd --uid 1000 --create-home appuser && chown appuser:appuser /app
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]

# ---------- runtime ----------
FROM base AS runtime
COPY --from=builder /opt/venv /opt/venv
RUN useradd --uid 1000 --create-home appuser
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser alembic.ini ./alembic.ini
USER appuser
EXPOSE 8080
# No curl in the image on purpose - the healthcheck uses the interpreter we already have.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
