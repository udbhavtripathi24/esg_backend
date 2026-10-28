"""assurance and evidence

Adds:
- evidence_files: supporting documents behind BRSR answers;
- assurance_engagements / _members / _items / _requests / _findings;
- brsr_sections.internal_reviewed_* (two-tier review) and
  company.brsr_internal_review_required (default false: behaviour unchanged);
- RBAC data: assurance:* permissions, the Assurance Partner and Assurance
  Practitioner roles, and their grants (idempotent; matches
  app/rbac/definitions.py).

Revision ID: e6b2d9a4c7f1
Revises: c4e8a1d6f2b9
Create Date: 2026-10-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'e6b2d9a4c7f1'
down_revision: Union[str, Sequence[str], None] = 'c4e8a1d6f2b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('assurance_engagements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('response_set_id', sa.Integer(), nullable=False),
    sa.Column('financial_year', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('level', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('standard', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('provider_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('scope', sa.JSON(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('fieldwork_started_at', sa.DateTime(), nullable=True),
    sa.Column('eqr_cleared_by', sa.Integer(), nullable=True),
    sa.Column('eqr_cleared_at', sa.DateTime(), nullable=True),
    sa.Column('eqr_note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('signed_by', sa.Integer(), nullable=True),
    sa.Column('signed_at', sa.DateTime(), nullable=True),
    sa.Column('opinion', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('opinion_basis', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('statement_text', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('signed_content_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('statement_storage_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('statement_sha256', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('register_storage_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('register_sha256', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('signed_register', sa.JSON(), nullable=True),
    sa.Column('withdrawn_by', sa.Integer(), nullable=True),
    sa.Column('withdrawn_at', sa.DateTime(), nullable=True),
    sa.Column('withdrawn_reason', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('superseded_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['superseded_by_id'], ['assurance_engagements.id'], ),
    sa.ForeignKeyConstraint(['company_id'], ['company.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['user.id'], ),
    sa.ForeignKeyConstraint(['eqr_cleared_by'], ['user.id'], ),
    sa.ForeignKeyConstraint(['response_set_id'], ['brsr_response_sets.id'], ),
    sa.ForeignKeyConstraint(['signed_by'], ['user.id'], ),
    sa.ForeignKeyConstraint(['withdrawn_by'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_assurance_engagements_company_id'), 'assurance_engagements', ['company_id'], unique=False)
    op.create_index(op.f('ix_assurance_engagements_public_id'), 'assurance_engagements', ['public_id'], unique=True)
    op.create_index(op.f('ix_assurance_engagements_response_set_id'), 'assurance_engagements', ['response_set_id'], unique=False)
    op.create_index(op.f('ix_assurance_engagements_status'), 'assurance_engagements', ['status'], unique=False)
    op.create_index('uq_assurance_active_per_set', 'assurance_engagements', ['response_set_id'], unique=True,
                    postgresql_where=sa.text("status IN ('planning', 'fieldwork', 'signed')"))
    op.create_table('assurance_findings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('engagement_id', sa.Integer(), nullable=False),
    sa.Column('section_code', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('question_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('severity', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('raised_by', sa.Integer(), nullable=False),
    sa.Column('raised_at', sa.DateTime(), nullable=False),
    sa.Column('management_response', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('responded_by', sa.Integer(), nullable=True),
    sa.Column('responded_at', sa.DateTime(), nullable=True),
    sa.Column('resolved_by', sa.Integer(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('resolution_note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['engagement_id'], ['assurance_engagements.id'], ),
    sa.ForeignKeyConstraint(['raised_by'], ['user.id'], ),
    sa.ForeignKeyConstraint(['resolved_by'], ['user.id'], ),
    sa.ForeignKeyConstraint(['responded_by'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_assurance_finding_eng_status', 'assurance_findings', ['engagement_id', 'status'], unique=False)
    op.create_index(op.f('ix_assurance_findings_public_id'), 'assurance_findings', ['public_id'], unique=True)
    op.create_table('assurance_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('engagement_id', sa.Integer(), nullable=False),
    sa.Column('section_code', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('procedures', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('conclusion', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('prepared_by', sa.Integer(), nullable=True),
    sa.Column('prepared_at', sa.DateTime(), nullable=True),
    sa.Column('reviewed_by', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('review_note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['engagement_id'], ['assurance_engagements.id'], ),
    sa.ForeignKeyConstraint(['prepared_by'], ['user.id'], ),
    sa.ForeignKeyConstraint(['reviewed_by'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('engagement_id', 'section_code', name='uq_assurance_item')
    )
    op.create_index(op.f('ix_assurance_items_engagement_id'), 'assurance_items', ['engagement_id'], unique=False)
    op.create_table('assurance_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('engagement_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('added_by', sa.Integer(), nullable=False),
    sa.Column('added_at', sa.DateTime(), nullable=False),
    sa.Column('independence_declared_at', sa.DateTime(), nullable=True),
    sa.Column('removed_at', sa.DateTime(), nullable=True),
    sa.Column('removed_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['added_by'], ['user.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['assurance_engagements.id'], ),
    sa.ForeignKeyConstraint(['removed_by'], ['user.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('engagement_id', 'user_id', name='uq_assurance_member')
    )
    op.create_index(op.f('ix_assurance_members_engagement_id'), 'assurance_members', ['engagement_id'], unique=False)
    op.create_index(op.f('ix_assurance_members_user_id'), 'assurance_members', ['user_id'], unique=False)
    op.create_table('assurance_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('engagement_id', sa.Integer(), nullable=False),
    sa.Column('section_code', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('question_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('detail', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('requested_by', sa.Integer(), nullable=False),
    sa.Column('requested_at', sa.DateTime(), nullable=False),
    sa.Column('response_text', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('responded_by', sa.Integer(), nullable=True),
    sa.Column('responded_at', sa.DateTime(), nullable=True),
    sa.Column('closed_by', sa.Integer(), nullable=True),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.Column('history', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['closed_by'], ['user.id'], ),
    sa.ForeignKeyConstraint(['engagement_id'], ['assurance_engagements.id'], ),
    sa.ForeignKeyConstraint(['requested_by'], ['user.id'], ),
    sa.ForeignKeyConstraint(['responded_by'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_assurance_request_eng_status', 'assurance_requests', ['engagement_id', 'status'], unique=False)
    op.create_index(op.f('ix_assurance_requests_public_id'), 'assurance_requests', ['public_id'], unique=True)
    op.create_table('evidence_files',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('response_set_id', sa.Integer(), nullable=False),
    sa.Column('section_code', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('question_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('assurance_request_id', sa.Integer(), nullable=True),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('original_filename', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('mime_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('sha256_checksum', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('storage_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('uploaded_by', sa.Integer(), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.Column('deleted_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['assurance_request_id'], ['assurance_requests.id'], ),
    sa.ForeignKeyConstraint(['company_id'], ['company.id'], ),
    sa.ForeignKeyConstraint(['deleted_by'], ['user.id'], ),
    sa.ForeignKeyConstraint(['response_set_id'], ['brsr_response_sets.id'], ),
    sa.ForeignKeyConstraint(['uploaded_by'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('storage_key')
    )
    op.create_index(op.f('ix_evidence_files_assurance_request_id'), 'evidence_files', ['assurance_request_id'], unique=False)
    op.create_index(op.f('ix_evidence_files_company_id'), 'evidence_files', ['company_id'], unique=False)
    op.create_index(op.f('ix_evidence_files_public_id'), 'evidence_files', ['public_id'], unique=True)
    op.create_index('ix_evidence_set_section', 'evidence_files', ['response_set_id', 'section_code'], unique=False)
    op.add_column('brsr_sections', sa.Column('internal_reviewed_by', sa.Integer(), nullable=True))
    op.add_column('brsr_sections', sa.Column('internal_reviewed_at', sa.DateTime(), nullable=True))
    op.add_column('brsr_sections', sa.Column('internal_review_note', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_foreign_key('fk_brsr_sections_internal_reviewed_by', 'brsr_sections', 'user',
                          ['internal_reviewed_by'], ['id'])
    op.add_column('company', sa.Column('brsr_internal_review_required', sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
    _rbac_upgrade()


def downgrade() -> None:
    """Downgrade schema."""
    _rbac_downgrade()
    op.drop_column('company', 'brsr_internal_review_required')
    op.drop_constraint('fk_brsr_sections_internal_reviewed_by', 'brsr_sections', type_='foreignkey')
    op.drop_column('brsr_sections', 'internal_review_note')
    op.drop_column('brsr_sections', 'internal_reviewed_at')
    op.drop_column('brsr_sections', 'internal_reviewed_by')
    op.drop_index('ix_evidence_set_section', table_name='evidence_files')
    op.drop_index(op.f('ix_evidence_files_public_id'), table_name='evidence_files')
    op.drop_index(op.f('ix_evidence_files_company_id'), table_name='evidence_files')
    op.drop_index(op.f('ix_evidence_files_assurance_request_id'), table_name='evidence_files')
    op.drop_table('evidence_files')
    op.drop_index(op.f('ix_assurance_requests_public_id'), table_name='assurance_requests')
    op.drop_index('ix_assurance_request_eng_status', table_name='assurance_requests')
    op.drop_table('assurance_requests')
    op.drop_index(op.f('ix_assurance_members_user_id'), table_name='assurance_members')
    op.drop_index(op.f('ix_assurance_members_engagement_id'), table_name='assurance_members')
    op.drop_table('assurance_members')
    op.drop_index(op.f('ix_assurance_items_engagement_id'), table_name='assurance_items')
    op.drop_table('assurance_items')
    op.drop_index(op.f('ix_assurance_findings_public_id'), table_name='assurance_findings')
    op.drop_index('ix_assurance_finding_eng_status', table_name='assurance_findings')
    op.drop_table('assurance_findings')
    op.drop_index('uq_assurance_active_per_set', table_name='assurance_engagements')
    op.drop_index(op.f('ix_assurance_engagements_status'), table_name='assurance_engagements')
    op.drop_index(op.f('ix_assurance_engagements_response_set_id'), table_name='assurance_engagements')
    op.drop_index(op.f('ix_assurance_engagements_public_id'), table_name='assurance_engagements')
    op.drop_index(op.f('ix_assurance_engagements_company_id'), table_name='assurance_engagements')
    op.drop_table('assurance_engagements')


# ---------------------------------------------------------------------------
# RBAC data (kept in step with app/rbac/definitions.py)
# ---------------------------------------------------------------------------

NEW_PERMISSIONS = [
    ("assurance:read", "View assurance engagements and their status"),
    ("assurance:respond", "Answer assurance information requests and findings"),
    ("assurance:manage", "Create assurance engagements and assign the team"),
    ("assurance:perform", "Perform assurance fieldwork: workpapers, requests, findings"),
    ("assurance:sign", "Sign the assurance opinion"),
]
NEW_ROLES = [
    ("Assurance Partner", "deloitte", "Signs assurance opinions; leads assurance engagements"),
    ("Assurance Practitioner", "deloitte", "Performs assurance fieldwork on assigned engagements"),
]
_BASE = ["company:read", "dataset:read", "comment:read", "report:read", "file:download", "notification:read",
         "assurance:read", "assurance:perform"]
GRANTS = {
    "Administrator": ["assurance:read", "assurance:manage"],
    "Consultant": ["assurance:read"],
    "Reviewer": ["assurance:read"],
    "Support": ["assurance:read"],
    "Client Administrator": ["assurance:read", "assurance:respond"],
    "Client Reviewer": ["assurance:read", "assurance:respond"],
    "Client Uploader": ["assurance:read", "assurance:respond"],
    "Client Approver": ["assurance:read", "assurance:respond"],
    "Assurance Partner": _BASE + ["assurance:sign", "assurance:manage"],
    "Assurance Practitioner": _BASE,
}


def _rbac_upgrade() -> None:
    conn = op.get_bind()
    for code, desc in NEW_PERMISSIONS:
        conn.execute(sa.text("INSERT INTO permissions (code, description, created_at) VALUES (:c, :d, now()) "
                             "ON CONFLICT (code) DO NOTHING"), {"c": code, "d": desc})
    for code, scope, desc in NEW_ROLES:
        conn.execute(sa.text("INSERT INTO roles (code, scope, description, created_at) VALUES (:c, :s, :d, now()) "
                             "ON CONFLICT (code) DO NOTHING"), {"c": code, "s": scope, "d": desc})
    for role, perms in GRANTS.items():
        for perm in perms:
            # Roles that do not exist in this database (never seeded) are skipped.
            conn.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = :r AND p.code = :p "
                "ON CONFLICT ON CONSTRAINT uq_role_permission DO NOTHING"), {"r": role, "p": perm})


def _rbac_downgrade() -> None:
    conn = op.get_bind()
    held = conn.execute(sa.text(
        "SELECT count(*) FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE r.code IN :codes"
    ).bindparams(sa.bindparam("codes", expanding=True)), {"codes": [r[0] for r in NEW_ROLES]}).scalar()
    if held:
        raise RuntimeError(f"{held} user(s) still hold an assurance role; remove those role assignments before "
                           f"downgrading")
    perm_codes = [p[0] for p in NEW_PERMISSIONS]
    conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id IN "
                         "(SELECT id FROM permissions WHERE code IN :codes)")
                 .bindparams(sa.bindparam("codes", expanding=True)), {"codes": perm_codes})
    conn.execute(sa.text("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE code IN :codes)")
                 .bindparams(sa.bindparam("codes", expanding=True)), {"codes": [r[0] for r in NEW_ROLES]})
    conn.execute(sa.text("DELETE FROM roles WHERE code IN :codes")
                 .bindparams(sa.bindparam("codes", expanding=True)), {"codes": [r[0] for r in NEW_ROLES]})
    conn.execute(sa.text("DELETE FROM permissions WHERE code IN :codes")
                 .bindparams(sa.bindparam("codes", expanding=True)), {"codes": perm_codes})
