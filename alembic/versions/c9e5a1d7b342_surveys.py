"""Surveys & assessments: one engine for client-built surveys and scored assessments.

- surveys: definition (questions as JSON), schedule, audience settings,
  public link token. Templates are surveys with is_template = true.
- survey_invites: who is asked (platform user or e-mail with a personal link;
  only the SHA-256 of the link token is stored).
- survey_responses: answers and score; identity removed on submission of an
  anonymous survey.
- survey_files: files given in answer to file-upload questions.

Hand-written, following the convention of the earlier migrations.

Revision ID: c9e5a1d7b342
Revises: b8d4f2a6c913
"""
import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "c9e5a1d7b342"
down_revision = "b8d4f2a6c913"
branch_labels = None
depends_on = None

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    op.create_table(
        "surveys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", S(), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id"), nullable=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("is_template", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kind", S(), nullable=False, server_default="survey"),
        sa.Column("category", S(), nullable=False, server_default="Custom"),
        sa.Column("title", S(), nullable=False),
        sa.Column("description", S(), nullable=True),
        sa.Column("welcome", S(), nullable=True),
        sa.Column("thank_you", S(), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", S(), nullable=False, server_default="draft"),
        sa.Column("opens_at", sa.DateTime(), nullable=True),
        sa.Column("closes_at", sa.DateTime(), nullable=True),
        sa.Column("anonymous", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("open_to_company", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("show_score_to_respondent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("public_token", S(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("kind IN ('survey', 'assessment')", name="ck_survey_kind"),
        sa.CheckConstraint("status IN ('draft', 'open', 'closed', 'archived')", name="ck_survey_status"),
    )
    op.create_index("ix_surveys_public_id", "surveys", ["public_id"], unique=True)
    op.create_index("ix_surveys_company_id", "surveys", ["company_id"])
    op.create_index("ix_surveys_is_template", "surveys", ["is_template"])
    op.create_index("ix_surveys_status", "surveys", ["status"])
    op.create_index("ix_surveys_public_token", "surveys", ["public_token"])
    op.create_index("ix_survey_company_status", "surveys", ["company_id", "status"])

    op.create_table(
        "survey_invites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", S(), nullable=False),
        sa.Column("survey_id", sa.Integer(), sa.ForeignKey("surveys.id"), nullable=False),
        sa.Column("channel", S(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("email", S(), nullable=True),
        sa.Column("name", S(), nullable=True),
        sa.Column("group_label", S(), nullable=True),
        sa.Column("token_hash", S(), nullable=True),
        sa.Column("status", S(), nullable=False, server_default="pending"),
        sa.Column("invited_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("invited_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("last_reminded_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("survey_id", "user_id", name="uq_survey_invite_user"),
        sa.UniqueConstraint("survey_id", "email", name="uq_survey_invite_email"),
        sa.CheckConstraint("channel IN ('user', 'email')", name="ck_survey_invite_channel"),
        sa.CheckConstraint("status IN ('pending', 'started', 'submitted')", name="ck_survey_invite_status"),
    )
    op.create_index("ix_survey_invites_public_id", "survey_invites", ["public_id"], unique=True)
    op.create_index("ix_survey_invites_user_id", "survey_invites", ["user_id"])
    op.create_index("ix_survey_invites_token_hash", "survey_invites", ["token_hash"])
    op.create_index("ix_survey_invite_survey_status", "survey_invites", ["survey_id", "status"])

    op.create_table(
        "survey_responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", S(), nullable=False),
        sa.Column("survey_id", sa.Integer(), sa.ForeignKey("surveys.id"), nullable=False),
        sa.Column("invite_id", sa.Integer(), sa.ForeignKey("survey_invites.id"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("respondent_name", S(), nullable=True),
        sa.Column("group_label", S(), nullable=True),
        sa.Column("channel", S(), nullable=False, server_default="user"),
        sa.Column("resume_key_hash", S(), nullable=True),
        sa.Column("status", S(), nullable=False, server_default="in_progress"),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("score", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('in_progress', 'submitted')", name="ck_survey_response_status"),
        sa.CheckConstraint("channel IN ('user', 'email', 'public')", name="ck_survey_response_channel"),
    )
    op.create_index("ix_survey_responses_public_id", "survey_responses", ["public_id"], unique=True)
    op.create_index("ix_survey_responses_resume_key_hash", "survey_responses", ["resume_key_hash"])
    op.create_index("ix_survey_response_survey_status", "survey_responses", ["survey_id", "status"])

    op.create_table(
        "survey_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", S(), nullable=False),
        sa.Column("survey_id", sa.Integer(), sa.ForeignKey("surveys.id"), nullable=False),
        sa.Column("response_id", sa.Integer(), sa.ForeignKey("survey_responses.id"), nullable=False),
        sa.Column("question_id", S(), nullable=False),
        sa.Column("storage_key", S(), nullable=False),
        sa.Column("original_filename", S(), nullable=False),
        sa.Column("mime_type", S(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256_checksum", S(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_survey_files_public_id", "survey_files", ["public_id"], unique=True)
    op.create_index("ix_survey_file_response", "survey_files", ["response_id"])


def downgrade() -> None:
    op.drop_index("ix_survey_file_response", table_name="survey_files")
    op.drop_index("ix_survey_files_public_id", table_name="survey_files")
    op.drop_table("survey_files")
    op.drop_index("ix_survey_response_survey_status", table_name="survey_responses")
    op.drop_index("ix_survey_responses_resume_key_hash", table_name="survey_responses")
    op.drop_index("ix_survey_responses_public_id", table_name="survey_responses")
    op.drop_table("survey_responses")
    op.drop_index("ix_survey_invite_survey_status", table_name="survey_invites")
    op.drop_index("ix_survey_invites_token_hash", table_name="survey_invites")
    op.drop_index("ix_survey_invites_user_id", table_name="survey_invites")
    op.drop_index("ix_survey_invites_public_id", table_name="survey_invites")
    op.drop_table("survey_invites")
    for ix in ("ix_survey_company_status", "ix_surveys_public_token", "ix_surveys_status",
               "ix_surveys_is_template", "ix_surveys_company_id", "ix_surveys_public_id"):
        op.drop_index(ix, table_name="surveys")
    op.drop_table("surveys")
