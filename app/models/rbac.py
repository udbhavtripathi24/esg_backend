"""RBAC models (Stage 2): roles, permissions, and their join tables.

Authoritative authorization source (decision #3). `users.role` string is a
denormalized display hint only and is NOT read for authz once RBAC is wired.

PK convention: integer PKs, matching the existing User/Company models. (The
UUID-vs-int question is deferred to a Stage-3 decision so Stage 2 does not
break the tested Auth/Company layer.)

Tenant compatibility (decision #8, item 8): `user_roles.company_id` is nullable
so a consultant can hold a role scoped to a specific client company. We add the
column now for forward-compat but Stage 2 does not implement company-scoped
resolution logic beyond storing it.
"""
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlmodel import SQLModel, Field, Relationship, UniqueConstraint

if TYPE_CHECKING:
    from app.models.user import User


class RolePermission(SQLModel, table=True):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    role_id: int = Field(foreign_key="roles.id", index=True)
    permission_id: int = Field(foreign_key="permissions.id", index=True)


class UserRole(SQLModel, table=True):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", "company_id", name="uq_user_role_company"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role_id: int = Field(foreign_key="roles.id", index=True)
    # Nullable: a consultant may hold a role scoped to one client company.
    # Null = the role applies globally to that user (e.g. a client admin over
    # their own single company, or a Deloitte-wide role).
    company_id: Optional[int] = Field(default=None, foreign_key="company.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Role(SQLModel, table=True):
    __tablename__ = "roles"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(unique=True, index=True)   # e.g. "Administrator"
    scope: str                                    # "deloitte" | "client"
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    permissions: List["Permission"] = Relationship(
        back_populates="roles",
        link_model=RolePermission,
    )


class Permission(SQLModel, table=True):
    __tablename__ = "permissions"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(unique=True, index=True)   # e.g. "dataset:review"
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    roles: List["Role"] = Relationship(
        back_populates="permissions",
        link_model=RolePermission,
    )
