# Stage 4 — ESG Data Foundation

**Status:** implemented and verified against real PostgreSQL 16.
**Tests:** 76 passing (35 from Stages 1–3, 41 new for Stage 4).
**Migration:** `c4a1e2b3d5f7_stage4_data_foundation.py`.

## What Stage 4 delivers

- **Master data:** sites, business units, departments (company-scoped, hierarchical, soft-delete)
- **Upload types registry:** metadata-only, seeded with the 4 MVP KPI slots + evidence
- **Datasets, versions, files:** immutable versioning, file role separation (data vs evidence)
- **Storage abstraction:** local filesystem for dev, GCS-ready adapter for prod
- **Upload API:** magic-byte validation (not extension trust), size limits, ZIP rejection, checksum
- **Signed URLs:** HMAC tokens verified by a redemption endpoint (defense in depth)
- **Processing jobs:** Postgres-backed durable queue with SKIP LOCKED, retry, idempotency
- **Worker:** poll-based, handler registry, runs as `python -m app.workers.runner`
- **Audit logging:** append-only, written in-transaction with every mutation
- **Integration boundary:** stub CRUD, no live connectors
- **Segregation of duties flag:** on `companies`, default true (enforced in Stage 5)
- **Public IDs:** integer PKs internally, prefixed public IDs (`st_`, `ds_`, `dv_`, `df_`) externally

## Local setup

```bash
# 1. Postgres (Ubuntu example)
sudo apt-get install -y postgresql postgresql-client
sudo -u postgres createuser -s esg
sudo -u postgres createdb esg_platform -O esg
sudo -u postgres psql -c "ALTER USER esg WITH PASSWORD 'esg';"

# 2. Python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install "bcrypt==4.0.1"     # passlib pins this version

# 3. Migrate + seed
export DATABASE_URL="postgresql+psycopg2://esg:esg@localhost:5432/esg_platform"
alembic upgrade head
python scripts_seed_rbac.py
python scripts_seed_upload_types.py

# 4. Run API
uvicorn app.main:app --reload

# 5. Run worker (separate terminal)
python -m app.workers.runner
```

## Storage backend selection

```bash
# Local (default, no credentials needed)
export STORAGE_BACKEND=local
export STORAGE_LOCAL_ROOT=./storage

# GCS (production)
export STORAGE_BACKEND=gcs
export STORAGE_BUCKET=my-vista-bucket
# + standard GOOGLE_APPLICATION_CREDENTIALS
```

## Running tests

```bash
pytest                    # 76 tests, SQLite-based (no Postgres needed)
python e2e_stage4.py      # 19 end-to-end checks vs. real Postgres + running API
```

## Design notes

- **Why Postgres-queue for jobs?** No infra dependency, full observability,
  retry/idempotency built-in, trivially swappable for Pub/Sub later. See
  `app/workers/__init__.py` for the SELECT FOR UPDATE SKIP LOCKED claim.
- **Why no OCR / Word parsing?** Approved decision 4: CSV/XLSX for structured
  data; PDF/DOCX are evidence only, never parsed for numbers.
- **Why keep integer PKs?** Approved decision 2. Public IDs handle
  enumeration-resistance externally.
- **Why company-scoped storage keys?** Defense in depth — even a bug that mixes
  company_ids in a query cannot produce a valid cross-tenant object key.
