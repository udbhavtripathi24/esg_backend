"""report generation foundation

Adds: reports, report_versions, report_snapshots, report_artifacts,
report_templates.

Hand-written, following the same convention as every prior migration in
this project. No changes to any existing table.

Revision ID: a2c9e4f7b1d3
Revises: f3a7b1e9c4d8
Create Date: 2026-09-04
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'a2c9e4f7b1d3'
down_revision: Union[str, Sequence[str], None] = 'f3a7b1e9c4d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'report_templates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('public_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('report_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_report_templates_public_id', 'report_templates', ['public_id'], unique=True)
    op.create_unique_constraint('uq_report_template_type_version', 'report_templates', ['report_type', 'version'])

    op.create_table(
        'reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('public_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('company.id'), nullable=False),
        sa.Column('report_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='esg_management_report'),
        sa.Column('reporting_period_start', sa.Date(), nullable=False),
        sa.Column('reporting_period_end', sa.Date(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        # Convenience pointer only -- FK added after report_versions exists (see below).
        sa.Column('current_version_id', sa.Integer(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='draft'),
    )
    op.create_index('ix_reports_public_id', 'reports', ['public_id'], unique=True)
    op.create_index('ix_report_company_type_period', 'reports', ['company_id', 'report_type', 'reporting_period_start'])

    op.create_table(
        'report_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('public_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('report_id', sa.Integer(), sa.ForeignKey('reports.id'), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='draft'),
        sa.Column('template_id', sa.Integer(), sa.ForeignKey('report_templates.id'), nullable=False),
        sa.Column('generator_version', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='v1'),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('generation_started_at', sa.DateTime(), nullable=True),
        sa.Column('generation_completed_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('reviewer_user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('failure_reason', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_index('ix_report_versions_public_id', 'report_versions', ['public_id'], unique=True)
    op.create_unique_constraint('uq_report_version_number', 'report_versions', ['report_id', 'version_number'])
    op.create_index('ix_report_version_report_status', 'report_versions', ['report_id', 'status'])

    # Now that report_versions exists, add the deferred convenience-pointer FK.
    op.create_foreign_key('fk_reports_current_version', 'reports', 'report_versions', ['current_version_id'], ['id'])

    op.create_table(
        'report_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('report_version_id', sa.Integer(), sa.ForeignKey('report_versions.id'), nullable=False),
        sa.Column('dataset_version_ids', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('kpi_value_ids', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('evidence_file_ids', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('configuration', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('content_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_unique_constraint('uq_report_snapshot_version', 'report_snapshots', ['report_version_id'])

    op.create_table(
        'report_artifacts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('public_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('report_version_id', sa.Integer(), sa.ForeignKey('report_versions.id'), nullable=False),
        sa.Column('format', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='docx'),
        sa.Column('storage_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('original_filename', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('mime_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256_checksum', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_report_artifacts_public_id', 'report_artifacts', ['public_id'], unique=True)
    op.create_unique_constraint('uq_report_artifact_version_format', 'report_artifacts', ['report_version_id', 'format'])


def downgrade() -> None:
    op.drop_table('report_artifacts')
    op.drop_table('report_snapshots')
    op.drop_constraint('fk_reports_current_version', 'reports', type_='foreignkey')
    op.drop_table('report_versions')
    op.drop_table('reports')
    op.drop_table('report_templates')
