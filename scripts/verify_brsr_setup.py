"""Creates the throwaway org/company/users for verify_brsr.py.
Run ONLY against the throwaway database (DATABASE_URL must point at esg_brsr_verify).
Usage: python -m scripts.verify_brsr_setup"""
import os
import sys

if "esg_brsr_verify" not in os.environ.get("DATABASE_URL", ""):
    sys.exit("Refusing to run: DATABASE_URL does not point at the throwaway database esg_brsr_verify")

from sqlmodel import Session, select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.consultant_assignment import ConsultantAssignment  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.rbac import Role, UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from app.rbac.seed import seed_rbac  # noqa: E402

with Session(engine) as s:
    seed_rbac(s)
    s.commit()
    org = Organization(name="Deloitte (verify)")
    s.add(org); s.commit(); s.refresh(org)
    co = Company(name="BRSR Verify Co", organization_id=org.id, status="Approved")
    s.add(co); s.commit(); s.refresh(co)

    def mk(email, portal, role, company=None):
        u = User(name=email.split("@")[0], email=email, portal_type=portal, role=role,
                 hashed_password=hash_password("verify-pass-123"),
                 company_id=company.id if company else None,
                 organization_id=org.id if portal == "deloitte" else None)
        s.add(u); s.commit(); s.refresh(u)
        r = s.exec(select(Role).where(Role.code == role)).first()
        s.add(UserRole(user_id=u.id, role_id=r.id, company_id=company.id if company else None)); s.commit()
        return u

    mk("verify.clientadmin@brsr.test", "client", "Client Administrator", co)
    c = mk("verify.deloitte@brsr.test", "deloitte", "Consultant")
    s.add(ConsultantAssignment(company_id=co.id, consultant_user_id=c.id, is_active=True)); s.commit()
    print("throwaway users created")
