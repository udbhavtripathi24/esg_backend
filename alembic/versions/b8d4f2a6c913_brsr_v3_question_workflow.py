"""BRSR v3: per-question SPOC/reviewer workflow and approved-data fingerprint.

- brsr_department_roles: which users answer (SPOC) / approve (reviewer) each
  department's questions at a company.
- brsr_question_status: per-question status (open → submitted → approved,
  or returned), with who/when and the answer revision concerned.
- brsr_sections.approved_data_hash: fingerprint of the figures built from
  the data sheets at Deloitte approval (detects later data changes).

Hand-written, following the convention of the earlier migrations.

Revision ID: b8d4f2a6c913
Revises: a7c3e9f15b20
"""
import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "b8d4f2a6c913"
down_revision = "a7c3e9f15b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("brsr_sections", sa.Column("approved_data_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    op.create_table(
        "brsr_department_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("department", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "department", "user_id", "role", name="uq_brsr_dept_role"),
        sa.CheckConstraint("role IN ('spoc', 'reviewer')", name="ck_brsr_dept_role_role"),
    )
    op.create_index("ix_brsr_dept_role_company", "brsr_department_roles", ["company_id"])
    op.create_index("ix_brsr_department_roles_user_id", "brsr_department_roles", ["user_id"])

    op.create_table(
        "brsr_question_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("response_set_id", sa.Integer(), sa.ForeignKey("brsr_response_sets.id"), nullable=False),
        sa.Column("section_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("question_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="open"),
        sa.Column("answer_revision", sa.Integer(), nullable=True),
        sa.Column("submitted_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("note", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("response_set_id", "question_id", name="uq_brsr_question_status"),
        sa.CheckConstraint("status IN ('open', 'submitted', 'approved', 'returned')", name="ck_brsr_qstatus_status"),
    )
    op.create_index("ix_brsr_qstatus_set_section", "brsr_question_status", ["response_set_id", "section_code"])


def downgrade() -> None:
    op.drop_index("ix_brsr_qstatus_set_section", table_name="brsr_question_status")
    op.drop_table("brsr_question_status")
    op.drop_index("ix_brsr_department_roles_user_id", table_name="brsr_department_roles")
    op.drop_index("ix_brsr_dept_role_company", table_name="brsr_department_roles")
    op.drop_table("brsr_department_roles")
    op.drop_column("brsr_sections", "approved_data_hash")
