"""final_results.final_tag_model: the model whose answer became final_tag (pass 2's for an agreed Misc, else pass 3's)

Revision ID: final_tag_model
Revises: api_name
Create Date: 2026-11-14
"""
import importlib.util
from pathlib import Path

from alembic import op

revision = 'final_tag_model'
down_revision = 'api_name'
branch_labels = None
depends_on = None


def _previous_view() -> str:
    spec = importlib.util.spec_from_file_location("previous", Path(__file__).with_name("misc_category.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._view(module._previous().FINAL_RESULTS_VIEW)


def _view(previous_sql: str) -> str:
    """The previous final_results with final_tag_model added as the last column."""
    old = "         WHEN needs_pass_3 THEN misc_category_3 END AS misc_category\nFROM routed"
    new = ("         WHEN needs_pass_3 THEN misc_category_3 END AS misc_category,\n"
           "    CASE WHEN misc_1 AND misc_2 THEN model_2\n"
           "         WHEN needs_pass_3 AND tag_3 IS NOT NULL THEN model_3 END AS final_tag_model\nFROM routed")
    assert previous_sql.count(old) == 1, old
    return previous_sql.replace(old, new)


def upgrade() -> None:
    op.execute(_view(_previous_view()))


def downgrade() -> None:
    op.execute("DROP VIEW final_results")  # a replaced view can't lose a column
    op.execute(_previous_view())
