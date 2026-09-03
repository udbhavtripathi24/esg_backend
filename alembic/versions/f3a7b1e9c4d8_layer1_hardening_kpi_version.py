"""layer1 hardening: kpi_definition_version provenance

Post-launch independent review requested closing a provenance gap: a
KpiValue could only be tied to a KpiDefinition by code alone, with no
record of WHICH version of that definition was active at extraction
time. Adds kpi_definition_version, backfilled to 1 for any existing rows
(every KpiDefinition is currently version 1 -- see
scripts_seed_kpi_definitions.py -- so this backfill is exact, not a
guess).

Revision ID: f3a7b1e9c4d8
Revises: e1f4a8c9d2b6
Create Date: 2026-09-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f3a7b1e9c4d8'
down_revision: Union[str, Sequence[str], None] = 'e1f4a8c9d2b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('kpi_values', sa.Column(
        'kpi_definition_version', sa.Integer(), nullable=False, server_default='1'
    ))


def downgrade() -> None:
    op.drop_column('kpi_values', 'kpi_definition_version')
