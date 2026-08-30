# Stage 0.5 — Architecture Design v1.1 (Final)
ESG Reporting Platform — Production GCP Backend

Status: **Final design. Incorporates all 15 approved decisions. Stage 1 (scaffolding only) begins after this document.**

Supersedes: Stage 0.5 v1.0. Changes from v1.0 are marked **[v1.1]**.

---

## 0. Decisions Locked In This Revision

| # | Decision | Where reflected |
|---|---|---|
| 1 | GCP is target cloud; domain logic stays cloud-neutral | §7, §8 |
| 2 | Keep SQLModel; SQLAlchemy underneath only where needed | §8 |
| 3 | Relational RBAC is authoritative (users→user_roles→roles→role_permissions→permissions); `users.role` is NOT the security source of truth | §1, §6 **[v1.1]** |
| 4 | `organizations` is a real entity; Deloitte is a seed row | §1 |
| 5 | Strict tenant isolation from authenticated identity, never client-supplied `company_id` | §6 |
| 6 | Keep `company_id` on query-critical tables; document parent/child consistency | §1, §6 **[v1.1]** |
| 7 | Event layer designed for a later outbox pattern | §4 **[v1.1]** |
| 8 | `processing_jobs` model for long-running file/report/integration jobs | §1, §3 **[v1.1]** |
| 9 | No executable formulas in DB; `formula_ref` points to versioned backend calc code | §5 |
| 10 | Dataset versions may use JSONB snapshots, but normalized ESG records stay queryable | §1, §5 **[v1.1]** |
| 11 | Analytics/BigQuery not blocked on Power BI; only PBI workspace/refresh is blocked | §7, §10 |
| 12 | Invent no business rules (scoring, weights, factors, benchmarking, regulatory) | §5, §9 |
| 13 | Explicit TODO/decision docs; hard blockers only for stages that need them | §9 **[v1.1]** |
| 14 | Benchmarking/Analytics FE migration stays behind the KPI/analytics foundation | §? migration plan |
| 15 | Frontend is the UI contract; migrate mockData.js incrementally (real API → test → replace) | migration plan |

---

## 1. Complete ERD v1.1

Global conventions (unchanged from v1.0 unless noted):
- `id UUID PK DEFAULT gen_random_uuid()`
- `company_id UUID` on tenant-owned/query-critical tables, indexed
- `created_at`, `updated_at TIMESTAMPTZ` (trigger-maintained)
- `deleted_at TIMESTAMPTZ NULL` soft-delete on business-data tables; hard-delete only for tokens; `audit_logs` never deleted

### Identity, Organization & RBAC **[v1.1 — RBAC expanded to relational]**

- **organizations** — `id`, `name`. Deloitte = seed row.
- **companies** — `id`, `organization_id→organizations.id`, `name`, `industry`, `country`, `sector`, `structure`, `plan`, `status`, `registration_date`.
- **users** — `id`, `company_id→companies.id NULL`, `organization_id→organizations.id NULL` (exactly one set, CHECK-enforced), `name`, `email UNIQUE`, `hashed_password`, `portal_type`, `department`, `is_active`. **`role` string retained ONLY as a denormalized display/back-compat hint — NOT read for authorization** (see §6).
- **roles** — `id`, `code` (Administrator/Consultant/Reviewer/Support/Uploader/Approver), `scope` (deloitte|client), `description`.
- **permissions** — `id`, `code` (e.g. `dataset:review`, `user:manage`, `report:generate`), `description`.
- **user_roles** — `id`, `user_id→users.id`, `role_id→roles.id`, `company_id NULL` (a consultant may hold a role scoped to a specific client). Unique `(user_id, role_id, company_id)`.
- **role_permissions** — `id`, `role_id→roles.id`, `permission_id→permissions.id`. Unique `(role_id, permission_id)`.
- **refresh_tokens** — `id`, `user_id`, `token_hash`, `expires_at`, `revoked_at NULL`. Hard-deleted by cleanup job.
- **consultant_assignments** — `id`, `company_id`, `consultant_user_id→users.id`, `role_on_account`. Unique `(company_id, consultant_user_id)`.
- **subscriptions** — `id`, `company_id`, `plan`, `start_date`, `end_date`, `status`.

### ESG Configuration
- **frameworks** — `id`, `code`, `name`, `category`. Global reference.
- **company_frameworks** — `id`, `company_id`, `framework_id`, `status`, `progress_pct`.
- **emission_factors** — `id`, `company_id NULL` (null=global), `name`, `category`, `value NUMERIC`, `unit`, `source`, `region`, `effective_date`, `expiry_date NULL`, `priority`, `version INT`. **Values remain placeholders until Open Question #3 resolved (§9).**

### Data & Files **[v1.1 — normalized records stay queryable]**
- **datasets** — `id`, `company_id`, `framework_id`, `kpi_domain`, `data_type`, `period`, `status`, `uploaded_by`, `assigned_reviewer_id NULL`, `current_version INT`.
- **dataset_files** — `id`, `dataset_id`, `company_id`, `original_filename`, `object_key`, `mime_type`, `size_bytes`, `checksum`, `uploaded_by`, `processing_status`, `file_role`.
- **dataset_records** — `id`, `dataset_id`, `company_id`, `row_index`, `site_ref`, `metric_code`, `metric_value NUMERIC`, `unit`, `period`. **Normalized, fully queryable for analytics — NOT stored only as JSON.** This is the structural backbone the KPI engine reads.
- **dataset_versions** — `id`, `dataset_id`, `company_id`, `version_number`, `snapshot_data JSONB`, `created_by`. **JSONB is for historical/version metadata only**; live analytics always reads `dataset_records`, never the JSON snapshot.
- **data_quality_runs** / **data_quality_issues** — as v1.0.

### Review / Workflow
- **review_comments**, **review_decisions**, **assurance_cases**, **assurance_findings**, **assurance_timeline_events** — as v1.0.

### Analytics
- **kpi_definitions** — `id`, `code`, `name`, `category`, `unit`, `formula_ref NULL`, `formula_version NULL`. `formula_ref` is a **string identifier resolving to a registered, versioned calculation function in backend code — never an executable expression stored in the DB** (§5, decision #9).
- **kpi_values** — `id`, `company_id`, `kpi_definition_id`, `dataset_id NULL`, `period`, `value NUMERIC`, `calculated_at`, `formula_version` (which calc version produced it).
- **kpi_snapshots** — append-only historical.
- **benchmark_results** — `id`, `company_id`, `period`, `industry_avg`, `best_performer`, `percentile_rank`, `pillar_scores JSONB`. **Methodology unresolved (§9).**
- **targets** — as v1.0.

### Assessment (unified)
- **assessments** (`kind` = assessment|survey), **assessment_questions**, **assessment_responses** — as v1.0.

### Platform **[v1.1 — processing_jobs added, events outbox-ready]**
- **audit_logs** — immutable append-only; `organization_id NULL`, `company_id NULL`, `actor_id NULL`, `event`, `message`, `record_type`, `record_id`, `ip_address`, `request_id`, `created_at`.
- **notifications** — `id`, `user_id`, `company_id NULL`, `event`, `message`, `read_at NULL`, `link NULL`.
- **integrations** / **integration_runs** — as v1.0; credentials via Secret Manager ref only.
- **processing_jobs** **[v1.1, decision #8]** — `id`, `company_id NULL`, `job_type` (file_processing|report_generation|integration_sync), `resource_type`, `resource_id`, `status` (see §3), `attempt INT DEFAULT 0`, `started_at NULL`, `finished_at NULL`, `error NULL`, `correlation_id` (== request_id that triggered it). Powers retry/attempt tracking for every long-running job.
- **event_outbox** **[v1.1, decision #7 — table designed now, populated later]** — `id`, `aggregate_type`, `aggregate_id`, `event_type`, `payload JSONB`, `dedupe_key`, `created_at`, `published_at NULL`, `attempts INT DEFAULT 0`. Not written to in Stage 1; exists so critical events (`review.decided`, `dataset.processed`, `report.requested`) can be persisted in the same DB transaction as the state change and published to Pub/Sub by a relay, so a Pub/Sub failure never silently drops them.

### Reporting
- **report_definitions**, **report_jobs** (may be modeled via `processing_jobs` with `job_type=report_generation` — see §3), **report_artifacts** — as v1.0.

---

## 2. API Contract v1.1

Unchanged from v1.0 in shape (REST, `/api/v1`, bearer auth, `{items,total,page,page_size}` pagination, `{error:{code,message,field}}` errors, 404-not-403 cross-tenant). Authoritative resource list unchanged. **[v1.1]** Authorization on every protected route is now expressed as a **permission requirement** (e.g. `require_permission("dataset:review")`) resolved through relational RBAC, not a role-string check.

---

## 3. State Machines v1.1

Dataset, Review, Assurance, Task, Report, File-processing — all unchanged from v1.0.

**[v1.1] processing_jobs** (unifying state for file/report/integration jobs):
`queued → running → (succeeded | failed)`; `failed` with `attempt < max` → `queued` (retry); terminal `failed` after max attempts. `report_jobs` and `dataset_files.processing_status` are specializations of this shared lifecycle.

---

## 4. Event Catalog v1.1 **[outbox-ready]**

All events, producers, consumers, sync/async, retry, idempotency — as v1.0. **Addition:** the three critical domain events — `review.decided`, `dataset.processed`, `report.requested` — are designated **outbox events**: their producers will (in a later stage) write to `event_outbox` in the *same DB transaction* as the state change, and a relay publishes to Pub/Sub. Stage 1 does not implement the relay, but the event-emitting service methods are structured so the emit call is a single swappable seam (direct-publish now → outbox-write later) with no call-site changes.

---

## 5. KPI Architecture v1.1

Unchanged from v1.0, reinforced by decisions #9, #10, #12:
- `formula_ref` identifies a **registered, versioned calculation function in the backend calc layer** — no executable formula text in the DB.
- The calc layer reads **normalized `dataset_records`** (queryable), not JSON snapshots.
- `kpi_values.formula_version` records which calc version produced each value → full reproducibility.
- **No scoring formula, weights, factor values, or benchmarking methodology are invented.** Calc functions for these remain registered-but-unimplemented stubs that raise `NotImplementedError("blocked: see docs/decisions/esg-scoring.md")` until resolved.

---

## 6. Multi-Tenant Authorization Model v1.1 **[relational RBAC + tenant consistency]**

Four enforcement layers, all server-side:

1. **Authentication** — JWT carries `user_id`, `portal_type`, and tenant anchor (`company_id` for client users, `organization_id` for consultants). **The JWT does NOT carry roles/permissions** — those are resolved per-request from the DB so a permission change takes effect without re-login.
2. **Tenant resolution** — tenant context is derived from the authenticated identity:
   - Client user → their own `company_id` (from the user row).
   - Consultant → a target `company_id` is only valid if an active `consultant_assignments` row links them. **Never taken from a request body/query param.**
3. **Permission check** — `require_permission(code)` dependency resolves `user → user_roles → roles → role_permissions → permissions` and checks membership. Composes with the tenant check; both fail closed.
4. **Parent/child tenant consistency [v1.1, decision #6]** — denormalized `company_id` on child tables (e.g. `dataset_files.company_id` duplicating `datasets.company_id`) is enforced by: (a) service layer always sets child `company_id` from the validated parent, never from input; (b) a DB-level composite FK `(dataset_id, company_id)` referencing `datasets(id, company_id)` so a child row physically cannot reference a parent in a different tenant. This makes contradictory `company_id` values impossible, not just discouraged.

**[v1.1] Migration note on existing code:** the current `app/api/deps.py` `require_role()` checks the single `user.role` string. This is a **Stage-0 placeholder that will be replaced** by `require_permission()` backed by relational RBAC. It is acceptable to leave it in place through Stage 1 scaffolding (it still gates the existing Auth/Company routes), but it is explicitly flagged as not the final authorization mechanism and must not be extended to new routes.

---

## 7. GCP Resource Map v1.1

Cloud Run (API + separate worker service), Cloud SQL PostgreSQL (private IP), Cloud Storage, Pub/Sub (topics per event family), Cloud Scheduler, Secret Manager, Artifact Registry, Cloud Logging + Monitoring, IAM (least-privilege SA per service). **BigQuery + the analytics data model are built independently of Power BI** (decision #11); only Power BI workspace/semantic-model/refresh is gated on actual PBI access.

---

## 8. ORM Decision v1.1 — SQLModel Retained (locked)

Confirmed by decision #2. The existing tested Auth/Company/User layer (inspected: `app/models/user.py`, `company.py`, `deps.py`, `security.py`, `db/session.py`) stays on SQLModel and is extended, not migrated. SQLAlchemy Core/ORM may be dropped to only where SQLModel's relationship/query ergonomics are insufficient (e.g. the RBAC many-to-many resolution, recursive/complex analytics queries). No rewrite of working code.

---

## 9. Unresolved Business Rules — Hard Blockers (decision #12, #13)

These get explicit decision docs (created in Stage 1 as empty-but-tracked files under `docs/decisions/`), and block **only** the stages that need them:

| Unresolved rule | Blocks | Does NOT block |
|---|---|---|
| ESG scoring formula + pillar weights | KPI scoring stage, Benchmarking/Analytics FE migration | Schema, ingestion, RBAC, files, review workflow |
| Production emission-factor source/methodology | Emissions calc stage | `emission_factors` table + CRUD (placeholder values) |
| Benchmarking peer-group methodology | Benchmarking results stage | `benchmark_results` table |
| Regulatory interpretations | Framework-specific validation rules | Generic dataset validation |

Each becomes a `docs/decisions/*.md` with status `BLOCKED — needs business input`.

---

## Frontend Migration Plan v1.1 (decisions #14, #15)

Order unchanged from v1.0 (Users/Assignments → Datasets/Comments → Targets/Reports/Factors → Logs/Connections → Assessments). **Reinforced:** Benchmarking + Analytics migration stays **last**, behind the KPI/analytics foundation, so mock numbers are never swapped for fake API numbers. Per-export migration is strictly: build real endpoint → test it → then delete the corresponding `mockData.js` dependency. `mockData.js` remains the UI contract until each slice is cut over.

---

## Implementation Roadmap (unchanged 27-stage plan; Stage 1 defined next)

Stage 1 = backend foundation/scaffolding ONLY, per the explicit scope below. No KPI engine, scoring, benchmarking, Power BI, or integrations.

---

**End of design. Proceeding to Stage 1 scaffolding only. Will stop at end of Stage 1 and report; will not silently continue to Stage 2.**
