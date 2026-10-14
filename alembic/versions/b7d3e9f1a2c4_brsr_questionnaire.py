"""brsr questionnaire

Adds: brsr_response_sets, brsr_sections, brsr_answers,
brsr_answer_history, brsr_comments.

Hand-written, following the same convention as every prior migration in
this project. No changes to any existing table.

Revision ID: b7d3e9f1a2c4
Revises: a2c9e4f7b1d3
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'b7d3e9f1a2c4'
down_revision: Union[str, Sequence[str], None] = 'a2c9e4f7b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    op.create_table(
        'brsr_response_sets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('public_id', S(), nullable=False),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('company.id'), nullable=False),
        sa.Column('financial_year', S(), nullable=False),
        sa.Column('bank_version', S(), nullable=False),
        sa.Column('carried_forward_from_id', sa.Integer(), sa.ForeignKey('brsr_response_sets.id'), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('company_id', 'financial_year', name='uq_brsr_set_company_fy'),
    )
    op.create_index('ix_brsr_response_sets_public_id', 'brsr_response_sets', ['public_id'], unique=True)
    op.create_index('ix_brsr_response_sets_company_id', 'brsr_response_sets', ['company_id'])

    op.create_table(
        'brsr_sections',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('response_set_id', sa.Integer(), sa.ForeignKey('brsr_response_sets.id'), nullable=False),
        sa.Column('section_code', S(), nullable=False),
        sa.Column('status', S(), nullable=False, server_default='draft'),
        sa.Column('submission_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('submitted_by', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('decided_by', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('decision_note', S(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('response_set_id', 'section_code', name='uq_brsr_section'),
    )
    op.create_index('ix_brsr_sections_response_set_id', 'brsr_sections', ['response_set_id'])
    op.create_index('ix_brsr_sections_status', 'brsr_sections', ['status'])

    op.create_table(
        'brsr_answers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('response_set_id', sa.Integer(), sa.ForeignKey('brsr_response_sets.id'), nullable=False),
        sa.Column('section_code', S(), nullable=False),
        sa.Column('question_id', S(), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('response_set_id', 'question_id', name='uq_brsr_answer'),
    )
    op.create_index('ix_brsr_answer_set_section', 'brsr_answers', ['response_set_id', 'section_code'])

    op.create_table(
        'brsr_answer_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('response_set_id', sa.Integer(), sa.ForeignKey('brsr_response_sets.id'), nullable=False),
        sa.Column('question_id', S(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('source', S(), nullable=False, server_default='edit'),
        sa.Column('changed_by', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('changed_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_brsr_history_set_question', 'brsr_answer_history', ['response_set_id', 'question_id'])

    op.create_table(
        'brsr_comments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('public_id', S(), nullable=False),
        sa.Column('response_set_id', sa.Integer(), sa.ForeignKey('brsr_response_sets.id'), nullable=False),
        sa.Column('section_code', S(), nullable=False),
        sa.Column('question_id', S(), nullable=True),
        sa.Column('body', S(), nullable=False),
        sa.Column('kind', S(), nullable=False, server_default='review'),
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_by', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
    )
    op.create_index('ix_brsr_comments_public_id', 'brsr_comments', ['public_id'], unique=True)
    op.create_index('ix_brsr_comment_set_section', 'brsr_comments', ['response_set_id', 'section_code'])


def downgrade() -> None:
    op.drop_table('brsr_comments')
    op.drop_table('brsr_answer_history')
    op.drop_table('brsr_answers')
    op.drop_table('brsr_sections')
    op.drop_table('brsr_response_sets')
