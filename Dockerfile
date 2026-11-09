# syntax=docker/dockerfile:1.7

# ---------- base ----------
# Pinned by digest (the multi-arch index): a moved tag must never rebuild the image by
# surprise. Bump deliberately - see docs/decisions/infra.md, "Base image".
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS base
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
# Everything pinned: pip itself, the direct dependencies (==) and every transitive package
# (constraints.txt). A rebuild installs exactly what production runs - see
# docs/decisions/infra.md, "Dependencies are pinned".
COPY requirements.txt constraints.txt ./
RUN pip install pip==26.2.1 && pip install -r requirements.txt -c constraints.txt

# ---------- runtime-venv ----------
# The builder's venv without pip: production never runs pip, and pip vendors its own
# certifi, msgpack, urllib3 and a setuptools listing that Trivy reports (#263). Uninstalled
# in a stage of its own, so no runtime layer ever holds pip. The dev image keeps pip.
FROM builder AS runtime-venv
RUN python -m pip uninstall -y pip

# ---------- dev ----------
# Used only by compose.override.yaml. Adds test/lint tooling; source is bind-mounted.
FROM base AS dev
COPY --from=builder /opt/venv /opt/venv
COPY requirements-dev.txt constraints-dev.txt ./
RUN pip install -r requirements-dev.txt -c constraints-dev.txt
# uid/gid 1000 matches the default WSL user, so bind-mounted files stay writable
# /data: the mcp service's SQLite volume; a new named volume copies this owner
RUN useradd --uid 1000 --create-home appuser && chown appuser:appuser /app \
 && install -d -o appuser -g appuser /data
USER appuser
EXPOSE 8080
# Same probe as runtime (see there).
HEALTHCHECK --interval=60s --timeout=3s --start-period=30s --start-interval=2s --retries=3 \
  CMD ["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/8080 && printf 'GET /healthz HTTP/1.0\\r\\nHost: localhost\\r\\n\\r\\n' >&3 && IFS= read -r -t 2 line <&3 && [[ $line == *' 200 '* ]]"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]

# ---------- runtime ----------
FROM base AS runtime
COPY --from=runtime-venv /opt/venv /opt/venv
RUN useradd --uid 1000 --create-home appuser && install -d -o appuser -g appuser /data
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser bss_mcp ./bss_mcp
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser alembic.ini ./alembic.ini
# The vendored TMF specs: app/api/specschema.py checks the attributes that are documents
# to us against them (docs/decisions/tmf-api.md, "Party and customer attributes").
COPY --chown=appuser:appuser spec/*.yaml spec/*.json ./spec/
USER appuser
EXPOSE 8080
# No curl in the image on purpose, and no Python start per probe (1 vCPU on the VPS): bash's
# /dev/tcp sends GET /healthz and the status line must contain " 200 ". Exec form with
# bash - /bin/sh is dash, which has no /dev/tcp. read -t 2 ends a stalled answer before
# the 3 s timeout. Every 2 s while starting (start_interval, Engine 25+), then every 60 s.
# Informational only: nothing restarts an unhealthy container (docs/decisions/infra.md,
# "Healthchecks").
HEALTHCHECK --interval=60s --timeout=3s --start-period=30s --start-interval=2s --retries=3 \
  CMD ["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/8080 && printf 'GET /healthz HTTP/1.0\\r\\nHost: localhost\\r\\n\\r\\n' >&3 && IFS= read -r -t 2 line <&3 && [[ $line == *' 200 '* ]]"]
# The process count is WEB_CONCURRENCY, uvicorn's own variable (compose sets it; 1 by default).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
