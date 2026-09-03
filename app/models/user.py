"""User model (Stage 3 — added org link, timestamps, soft-delete).

`role` string stays as a denormalized display hint ONLY. Authorization uses
relational RBAC (users -> user_roles -> roles -> permissions), never this field.
"""
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from sqlmodel import SQLModel, Field, Relationship

if TYPE_CHECKING:
    from app.models.company import Company


class UserBase(SQLModel):
    name: str
    email: str = Field(unique=True, index=True)
    portal_type: str  # "deloitte" | "client"
    role: str  # display hint only; NOT authoritative for permissions
    department: Optional[str] = None
    company_id: Optional[int] = Field(default=None, foreign_key="company.id")
    organization_id: Optional[int] = Field(default=None, foreign_key="organizations.id")


class User(UserBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    hashed_password: str
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = Field(default=None, index=True)

    company: Optional["Company"] = Relationship(back_populates="users")


class UserCreate(UserBase):
    password: str
    role_code: Optional[str] = None  # RBAC role to assign on creation


class UserRead(UserBase):
    id: int
    is_active: bool
    created_at: datetime
    # NOTE: hashed_password intentionally excluded from all read schemas.


class UserWithPermissions(UserRead):
    """UserRead + the caller's resolved RBAC permission codes.

    Used ONLY by /auth/login and /auth/me. Deliberately NOT merged into
    UserRead itself, since UserRead is the response model for general user
    CRUD (e.g. GET /users/{id}) where returning one user's permissions in
    the context of another user's request would be meaningless/misleading.
    `role` is still present (inherited from UserBase) as a display hint only
    — see the note on User.role. Authorization must use `permissions`, never
    `role`.
    """
    permissions: list[str]


class UserUpdate(SQLModel):
    name: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserLogin(SQLModel):
    email: str
    password: str
