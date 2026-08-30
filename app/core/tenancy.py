"""Tenant authorization helpers (Stage 3).

Central place for "can this authenticated user act on this company?" logic.
Rules (approved architecture):
- Client user: only their own company_id.
- Deloitte consultant: only companies with an ACTIVE consultant_assignments row,
  UNLESS they hold an org-wide admin permission.
- Deloitte admin (company:manage / company:read at org level): all companies in
  their organization.
Cross-tenant resource access returns 404 (existence non-leakage), enforced by
callers raising NotFoundError when this returns False.

Company IDs are NEVER taken from request bodies for authorization — always from
the authenticated user + these checks.
"""
from sqlmodel import Session, select
from app.models.user import User
from app.models.company import Company
from app.models.consultant_assignment import ConsultantAssignment
from app.rbac.service import user_has_permission


def accessible_company_ids(session: Session, user: User) -> set[int] | None:
    """Return the set of company IDs this user may access, or None meaning
    'all companies in their organization' (for org-level admins)."""
    if user.portal_type == "client":
        return {user.company_id} if user.company_id else set()

    # Deloitte/organization user
    # Org-level admins with company:read see all org companies.
    if user_has_permission(session, user.id, "company:manage") or \
       user_has_permission(session, user.id, "company:read"):
        # Consultants also have company:read — narrow them to assignments unless
        # they can manage (admin). Managers get all; pure consultants get assigned.
        if user_has_permission(session, user.id, "company:manage"):
            return None  # all in org
    # Consultant: assigned companies only
    rows = session.exec(
        select(ConsultantAssignment).where(
            ConsultantAssignment.consultant_user_id == user.id,
            ConsultantAssignment.is_active == True,  # noqa: E712
        )
    ).all()
    return {r.company_id for r in rows}


def can_access_company(session: Session, user: User, company_id: int) -> bool:
    ids = accessible_company_ids(session, user)
    if ids is None:
        # org-wide admin: company must be in their organization
        company = session.get(Company, company_id)
        return company is not None and company.organization_id == user.organization_id
    return company_id in ids
