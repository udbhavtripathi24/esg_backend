from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlmodel import Session, select
from app.db.session import get_session
from app.models.user import User, UserCreate, UserRead, UserWithPermissions
from app.rbac.service import get_user_permissions
from app.core.security import hash_password, verify_password, create_access_token
from app.api.deps import get_current_user
from app.services.audit import log_action

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead)
def register(user_in: UserCreate, session: Session = Depends(get_session)):
    existing = session.exec(select(User).where(User.email == user_in.email)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    if user_in.portal_type == "client" and not user_in.company_id:
        raise HTTPException(status_code=400, detail="Client users must belong to a company")

    user = User(
        name=user_in.name,
        email=user_in.email,
        portal_type=user_in.portal_type,
        role=user_in.role,
        department=user_in.department,
        company_id=user_in.company_id,
        hashed_password=hash_password(user_in.password),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    # form_data.username carries the email (OAuth2 password flow convention)
    user = session.exec(select(User).where(User.email == form_data.username)).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive")

    token = create_access_token({"sub": str(user.id), "portal_type": user.portal_type, "role": user.role})
    permissions = sorted(get_user_permissions(session, user.id, user.company_id))
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": UserWithPermissions(**user.model_dump(), permissions=permissions),
    }


@router.get("/me", response_model=UserWithPermissions)
def read_me(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    permissions = sorted(get_user_permissions(session, current_user.id, current_user.company_id))
    return UserWithPermissions(**current_user.model_dump(), permissions=permissions)


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


@router.post("/me/change-password", status_code=204)
def change_own_password(
    body: ChangePasswordBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Lets any authenticated user (client or Deloitte) change their own
    password. Never accepts a target user id -- always operates on the
    authenticated actor themselves, via get_current_user, never a
    client-supplied identifier. Requires the real current password to
    be provided and verified first, exactly like the login flow's own
    verify_password check -- never a bare "set new password" with no
    proof of who's asking.
    """
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=422, detail="New password must be at least 8 characters")

    current_user.hashed_password = hash_password(body.new_password)
    session.add(current_user)
    log_action(session, current_user, "user.password_changed", "user", current_user.id, None,
              company_id=current_user.company_id, ip_address=request.client.host if request.client else None)
    session.commit()
