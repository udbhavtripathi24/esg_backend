# Data Model (through Stage 3)

## Tables
- **organizations** — top-level tenant. Deloitte = seed row.
- **companies** — client orgs. FK organization_id. Soft-delete via deleted_at.
  Core fields only; subscription/frameworks/contact/progress DEFERRED (see below).
- **user** — FK company_id (client) or organization_id (deloitte); exactly one.
  `role` is a display hint, NOT authoritative. Soft-delete via deleted_at.
- **roles / permissions / role_permissions / user_roles** — relational RBAC.
- **consultant_assignments** — Deloitte user ↔ company. is_active soft-removal.

## Deferred to later stages (kept on frontend mock for now)
- subscriptions (plan start/end dates)
- company_frameworks (GRI/SASB/etc per company + progress %)
- company contact person (name/title/phone/email)
- clientPool "progress" and "reportStatus" aggregates

These map to future entities per Stage 0.5 v1.1 ERD; not implemented in Stage 3.

## Soft-delete policy
companies.deleted_at, user.deleted_at set on delete; rows filtered from all
reads. consultant_assignments use is_active=False (history preserved).
