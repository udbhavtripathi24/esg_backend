# Multi-stage build for a small production image (Cloud Run target).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for psycopg2.
# libreoffice-writer-nogui + Carlito (metric-compatible with Calibri) power the
# BRSR PDF export (app/brsr/docx_report.py:docx_to_pdf). Without them the Word
# report still generates; PDF download returns a clear "not available" error.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    libreoffice-writer-nogui fonts-crosextra-carlito fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Cloud Run injects PORT; default 8080 locally.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
