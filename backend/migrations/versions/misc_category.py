"""misc_category: what a "Misc - Not a Liquor" item is (Non-Alcoholic Beverage, Food, Chips, ...), per pass and final

Revision ID: misc_category
Revises: no_brand_skips_pass_3
Create Date: 2026-11-13
"""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = 'misc_category'
down_revision = 'no_brand_skips_pass_3'
branch_labels = None
depends_on = None

TABLES = ("llm_tags", "llm_retags", "llm_final_tags")


def _previous():
    spec = importlib.util.spec_from_file_location("previous", Path(__file__).with_name("no_brand_skips_pass_3.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _view(previous_sql: str) -> str:
    """The previous final_results with misc_category added as the last column (CREATE OR REPLACE allows that)."""
    sql = previous_sql
    for old, new in [
        ("        f.skip_reason AS skip_reason_3, f.error AS error_3\n",
         "        f.skip_reason AS skip_reason_3, f.error AS error_3,\n"
         "        t.misc_category AS misc_category_1, r.misc_category AS misc_category_2,\n"
         "        f.misc_category AS misc_category_3\n"),
        # misc_category_3 is NULL unless pass 3 itself answered Misc
        ("    needs_pass_2, needs_pass_3, model_1, model_2, model_3\nFROM routed",
         "    needs_pass_2, needs_pass_3, model_1, model_2, model_3,\n"
         "    CASE WHEN misc_1 AND misc_2 THEN coalesce(misc_category_2, misc_category_1)\n"
         "         WHEN needs_pass_3 THEN misc_category_3 END AS misc_category\nFROM routed"),
    ]:
        assert sql.count(old) == 1, old
        sql = sql.replace(old, new)
    return sql


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("misc_category", sa.Text()))
    op.execute(_view(_previous().FINAL_RESULTS_VIEW))


def downgrade() -> None:
    op.execute("DROP VIEW final_results")  # a replaced view can't lose a column
    op.execute(_previous().FINAL_RESULTS_VIEW)
    for table in TABLES:
        op.drop_column(table, "misc_category")
