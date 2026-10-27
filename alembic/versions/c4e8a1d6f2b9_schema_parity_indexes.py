"""schema parity: indexes declared on the models but never migrated

The models declare these indexes (index=True); databases built by
`create_all` have them, databases built by Alembic alone (production) did
not. Uses IF NOT EXISTS so it is a no-op wherever they already exist.

Revision ID: c4e8a1d6f2b9
Revises: b7d3e9f1a2c4
Create Date: 2026-10-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c4e8a1d6f2b9'
down_revision: Union[str, Sequence[str], None] = 'b7d3e9f1a2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [
    ("ix_kpi_definitions_code", "kpi_definitions", "code"),
    ("ix_kpi_definitions_upload_type_id", "kpi_definitions", "upload_type_id"),
    ("ix_kpi_values_company_id", "kpi_values", "company_id"),
    ("ix_kpi_values_dataset_id", "kpi_values", "dataset_id"),
    ("ix_kpi_values_dataset_version_id", "kpi_values", "dataset_version_id"),
    ("ix_kpi_values_kpi_code", "kpi_values", "kpi_code"),
    ("ix_kpi_values_source_file_id", "kpi_values", "source_file_id"),
    ("ix_report_artifacts_report_version_id", "report_artifacts", "report_version_id"),
    ("ix_report_snapshots_report_version_id", "report_snapshots", "report_version_id"),
    ("ix_report_versions_report_id", "report_versions", "report_id"),
    ("ix_report_versions_status", "report_versions", "status"),
    ("ix_reports_company_id", "reports", "company_id"),
    ("ix_review_comments_dataset_version_id", "review_comments", "dataset_version_id"),
]


def upgrade() -> None:
    for name, table, column in INDEXES:
        op.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})')


def downgrade() -> None:
    for name, _table, _column in INDEXES:
        op.execute(f'DROP INDEX IF EXISTS {name}')
