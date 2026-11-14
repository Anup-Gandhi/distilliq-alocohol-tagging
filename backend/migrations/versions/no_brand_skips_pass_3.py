"""final_results: a pass 1 "item text has no brand" skip doesn't go to pass 3, unless pass 2 named the product

Revision ID: no_brand_skips_pass_3
Revises: 3e94d81e92f6
Create Date: 2026-11-13
"""
import importlib.util
from pathlib import Path

from alembic import op

revision = 'no_brand_skips_pass_3'
down_revision = '3e94d81e92f6'
branch_labels = None
depends_on = None

FINAL_RESULTS_VIEW = r"""
CREATE OR REPLACE VIEW final_results AS
WITH s AS (
    SELECT
        t.source_row_id, t.base_value, t.options,
        t.tag AS tag_1, r.tag AS tag_2, f.tag AS tag_3,
        t.model AS model_1, r.model AS model_2, f.model AS model_3,
        coalesce(lower(btrim(t.tag, '<> ')) = 'misc - not a liquor', false) AS misc_1,
        coalesce(lower(btrim(r.tag, '<> ')) = 'misc - not a liquor', false) AS misc_2,
        -- the model often adds detail after it: "item text has no brand (only generic words: ...)"
        coalesce(t.skip_reason ILIKE 'item text has no brand%', false) AS no_brand_1,
        t.skip_reason AS skip_reason_1,
        r.source_row_id IS NOT NULL AS has_2,
        f.source_row_id IS NOT NULL AS has_3,
        r.reason AS reason_2, f.reason AS reason_3, f.is_alcoholic AS is_alcoholic_3,
        f.skip_reason AS skip_reason_3, f.error AS error_3
    FROM llm_tags t
    LEFT JOIN llm_retags r USING (source_row_id)
    LEFT JOIN llm_final_tags f USING (source_row_id)
), routed AS (
    SELECT *,
        t_blank_or_misc AS needs_pass_2,
        CASE WHEN no_brand_1 THEN tag_2 IS NOT NULL AND NOT misc_2  -- pass 2 found a brand after all
             ELSE NOT misc_1 OR (has_2 AND NOT misc_2) END AS needs_pass_3
    FROM (SELECT *, tag_1 IS NULL OR misc_1 AS t_blank_or_misc FROM s) x
)
SELECT
    source_row_id, base_value, options, tag_1, tag_2, tag_3,
    CASE WHEN misc_1 AND misc_2 THEN 'Misc - Not a Liquor'
         WHEN needs_pass_3 THEN tag_3 END AS final_tag,
    CASE WHEN misc_1 AND misc_2 THEN 'misc: passes 1 and 2 agree'
         WHEN misc_1 AND NOT has_2 THEN 'waiting for pass 2'
         WHEN no_brand_1 AND NOT needs_pass_3 THEN 'skipped: item text has no brand'
         WHEN NOT has_3 THEN 'waiting for pass 3'
         WHEN tag_3 IS NOT NULL THEN 'named by pass 3'
         WHEN skip_reason_3 IS NOT NULL THEN 'pass 3 skipped'
         ELSE 'pass 3 failed' END AS status,
    CASE WHEN misc_1 AND misc_2 THEN false WHEN needs_pass_3 THEN is_alcoholic_3 END AS is_alcoholic,
    CASE WHEN misc_1 AND misc_2 THEN reason_2
         WHEN needs_pass_3 THEN coalesce(reason_3, skip_reason_3, error_3)
         WHEN no_brand_1 THEN skip_reason_1 END AS reason,
    needs_pass_2, needs_pass_3, model_1, model_2, model_3
FROM routed
"""


def upgrade() -> None:
    op.execute(FINAL_RESULTS_VIEW)


def downgrade() -> None:
    spec = importlib.util.spec_from_file_location("baseline", Path(__file__).with_name("0001_baseline.py"))
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    op.execute(baseline.FINAL_RESULTS_VIEW)
