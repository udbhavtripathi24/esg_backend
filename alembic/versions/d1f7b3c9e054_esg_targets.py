"""Admin console: ESG targets.

- esg_targets: a company's goal for a key figure (baseline → target by a
  financial year); yearly figures come from the approved data sheets or are
  typed in.

No RBAC changes: targets use the existing admin:manage permission.

Hand-written, following the convention of the earlier migrations.

Revision ID: d1f7b3c9e054
Revises: c9e5a1d7b342
"""
import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "d1f7b3c9e054"
down_revision = "c9e5a1d7b342"
branch_labels = None
depends_on = None

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    op.create_table(
        "esg_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", S(), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("metric", S(), nullable=False),
        sa.Column("title", S(), nullable=False),
        sa.Column("unit", S(), nullable=False),
        sa.Column("direction", S(), nullable=False, server_default="reduce"),
        sa.Column("baseline_fy", S(), nullable=False),
        sa.Column("baseline_value", sa.Float(), nullable=False),
        sa.Column("target_fy", S(), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("manual_values", sa.JSON(), nullable=False),
        sa.Column("notes", S(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("direction IN ('reduce', 'increase')", name="ck_esg_target_direction"),
    )
    op.create_index("ix_esg_targets_public_id", "esg_targets", ["public_id"], unique=True)
    op.create_index("ix_esg_targets_company_id", "esg_targets", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_esg_targets_company_id", table_name="esg_targets")
    op.drop_index("ix_esg_targets_public_id", table_name="esg_targets")
    op.drop_table("esg_targets")
