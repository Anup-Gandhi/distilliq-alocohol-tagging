"""api_name: the LLM config (secrets/<name>.json) each row was tagged with, per pass. Set on every new row.

Revision ID: api_name
Revises: failed_api_key
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op

revision = 'api_name'
down_revision = 'failed_api_key'
branch_labels = None
depends_on = None

TABLES = ("llm_tags", "llm_retags", "llm_final_tags")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("api_name", sa.Text()))


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "api_name")
