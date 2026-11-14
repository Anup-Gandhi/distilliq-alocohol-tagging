"""api_key: the key a failed row was tagged with, per pass (NULL unless error is set). Hidden from the web app.

Revision ID: failed_api_key
Revises: misc_category
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op

revision = 'failed_api_key'
down_revision = 'misc_category'
branch_labels = None
depends_on = None

TABLES = ("llm_tags", "llm_retags", "llm_final_tags")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("api_key", sa.Text()))


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "api_key")
