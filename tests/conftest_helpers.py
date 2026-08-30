"""Shared helpers for Stage 3 tests: build users/companies/roles and auth headers."""
from sqlmodel import select
from app.models.user import User
from app.models.company import Company
from app.models.organization import Organization
from app.models.rbac import Role, UserRole
from app.rbac.seed import seed_rbac
from app.core.security import hash_password, create_access_token


def bootstrap(session):
    seed_rbac(session)
    org = Organization(name="Deloitte")
    session.add(org); session.commit(); session.refresh(org)
    return org


def make_company(session, org, name="ACME", status="Approved"):
    c = Company(name=name, organization_id=org.id, status=status)
    session.add(c); session.commit(); session.refresh(c)
    return c


def make_user(session, email, portal_type, role_code, company=None, org=None):
    u = User(
        name=email.split("@")[0], email=email, portal_type=portal_type,
        role=role_code, hashed_password=hash_password("pw"),
        company_id=company.id if company else None,
        organization_id=org.id if (org and portal_type == "deloitte") else None,
    )
    session.add(u); session.commit(); session.refresh(u)
    role = session.exec(select(Role).where(Role.code == role_code)).first()
    if role:
        session.add(UserRole(user_id=u.id, role_id=role.id,
                             company_id=company.id if company else None))
        session.commit()
    return u


def auth(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"}
