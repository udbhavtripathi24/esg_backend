# Tenant Authorization (Stage 3)

Enforced in `app/core/tenancy.py`, never from client-supplied company_id.

## Rules
- **Client user**: only their own company_id.
- **Deloitte consultant**: only companies with an ACTIVE consultant_assignments
  row. Access is resolved per-request, NOT cached in the JWT.
- **Deloitte admin** (company:manage): all companies in their organization.

## Cross-tenant behavior
Requests for resources outside the caller's tenant return **404** (not 403) to
avoid leaking existence. Permission failures (lacking the required permission
entirely) return **403**.

## Key invariants (tested)
- Client cannot read/modify another company → 404.
- Client admin creating a user: company_id forced to their own, ignoring input.
- Consultant cannot access unassigned company.
- Non-Deloitte user cannot be assigned as a consultant.
- Password hashes never appear in responses.
- Users cannot deactivate themselves.
