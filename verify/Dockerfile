# syntax=docker/dockerfile:1
# One-shot verification image.  It contains both the Python toolchain
# (backend unit tests) and Node/npm (frontend production build), plus the
# full source.  Build context must be the repository root.
FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /project

# backend test dependencies (pytest, fastapi, httpx, uvicorn)
COPY backend/requirements.txt /project/backend/requirements.txt
RUN pip install --no-cache-dir -r /project/backend/requirements.txt

# frontend build dependencies (vite) so the one-shot build check works
COPY web/package.json web/package-lock.json /project/web/
RUN cd /project/web && npm ci

COPY backend /project/backend
COPY web /project/web
COPY verify/verify.py /project/verify.py

ENV PROJECT_ROOT=/project \
    NPM_BIN=npm \
    API_BASE_URL=http://api:8000

# One-shot: runs the four checks, prints the summary and exits with the
# aggregated bitmask (0 == all good).
CMD ["python", "/project/verify.py"]
