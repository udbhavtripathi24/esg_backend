# Local Development Policy

**Decision (locked):** Backend development through the functional stages runs on a
**local Python 3.12 virtual environment + local PostgreSQL** — NOT Docker.

Docker / containerization is a **dedicated later production-preparation stage**,
done once the backend is functionally complete and immediately before GCP
deployment. No development stage is blocked on Docker Desktop.

The `Dockerfile` and `docker-compose.yml` in this repo are **kept but deferred** —
they are the production packaging path, validated later, not the dev loop.

---

## Prerequisites

- **Python 3.12** (NOT 3.13/3.14 — SQLModel/Pydantic don't fully support those yet)
- **PostgreSQL 14+** running locally

## One-time setup

```bash
# 1. Create the database (once)
createdb esg_platform            # or: psql -c "CREATE DATABASE esg_platform;"
# Ensure a role that matches DATABASE_URL, or edit .env to your local creds.

# 2. Python env (Python 3.12 explicitly)
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Environment
cp .env.example .env
# Edit .env: set DATABASE_URL to your local Postgres, e.g.
#   DATABASE_URL=postgresql+psycopg2://<user>:<pass>@localhost:5432/esg_platform
```

## Everyday commands

```bash
source .venv/bin/activate

# Apply migrations
alembic upgrade head

# Run the API (http://localhost:8000, docs at /docs)
uvicorn app.main:app --reload --port 8000

# Run tests
pytest
```

## Creating a migration after a model change

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## Deferred (production-prep stage only)

- `docker compose up --build` for containerized local parity
- Multi-stage Docker image build for Cloud Run
- These are validated in the pre-GCP hardening stage, not during feature work.
