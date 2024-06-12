"""audit_log table with hash chain columns.

Revision ID: 0001
Revises:
Create Date: 2024-06-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(36), unique=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("roles", sa.String(512), nullable=False, server_default=""),
        sa.Column("org_id", sa.String(64), nullable=False),
        sa.Column("question_hash", sa.String(64), nullable=False),
        sa.Column("retrieved_chunk_ids", sa.Text, nullable=False, server_default="[]"),
        sa.Column("redaction_stats", sa.Text, nullable=False, server_default="{}"),
        sa.Column("guardrail_prompt", sa.String(16), nullable=False, server_default="ALLOW"),
        sa.Column("guardrail_response", sa.String(16), nullable=False, server_default="ALLOW"),
        sa.Column("guardrail_reason", sa.Text, nullable=False, server_default=""),
        sa.Column("llm_model", sa.String(128), nullable=False, server_default=""),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("citations", sa.Text, nullable=False, server_default="[]"),
        sa.Column("prev_hash", sa.String(64), nullable=False, server_default="0" * 64),
        sa.Column("row_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_org_id", "audit_log", ["org_id"])
    op.create_index("ix_audit_log_row_hash", "audit_log", ["row_hash"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_row_hash", table_name="audit_log")
    op.drop_index("ix_audit_log_org_id", table_name="audit_log")
    op.drop_index("ix_audit_log_user_id", table_name="audit_log")
    op.drop_index("ix_audit_log_ts", table_name="audit_log")
    op.drop_table("audit_log")
