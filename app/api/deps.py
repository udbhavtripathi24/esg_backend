from app.core.config import settings
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select
from app.db.session import get_session
from app.core.security import decode_access_token
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_error
    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_error
    user = session.get(User, int(user_id))
    if user is None or not user.is_active:
        raise credentials_error
    return user


def require_portal(portal_type: str):
    """Use as a dependency to restrict a route to one portal, e.g. require_portal('deloitte')."""

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.portal_type != portal_type:
            raise HTTPException(status_code=403, detail=f"Requires {portal_type} portal access")
        return user

    return _check




# ---------------------------------------------------------------------------
# RBAC (Stage 2): permission-based authorization is the authoritative mechanism.
# `require_role` above is a DEPRECATED Stage-0 placeholder kept only so the
# existing Auth/Company routes keep working. Do NOT use it on new routes.
# ---------------------------------------------------------------------------
from app.rbac.service import user_has_permission  # noqa: E402


def require_permission(permission_code: str):
    """Route dependency enforcing a single permission via relational RBAC.

    Resolves the user's effective permissions from the DB each request (not the
    JWT), so grants/revocations apply without re-login. Returns 403 on denial.
    """

    def _check(
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> User:
        # company_id scoping: for client users, their own company; for consultants,
        # company-agnostic roles apply (company-scoped consultant resolution is a
        # later stage). This keeps tenant foundations compatible without expanding
        # Stage 2 into full company authorization.
        company_id = user.company_id
        if not user_has_permission(session, user.id, permission_code, company_id):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return _check
