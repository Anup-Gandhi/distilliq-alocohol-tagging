"""Baseline: the schema as it stood when Alembic was added (November 2026).

Safe on any database: an empty one, one created by the pre-Alembic `Store` (tables are only created when missing,
the old ADD COLUMN IF NOT EXISTS statements are re-run, views are CREATE OR REPLACE with the same SQL).
The view SQL was moved here verbatim from tagger/store.py; copy it exactly when changing a view in a new revision.

Revision ID: 0001_baseline
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# Column changes made by the pre-Alembic versions; no-ops on a database created here.
MIGRATIONS = [
    "ALTER TABLE llm_retags ADD COLUMN IF NOT EXISTS skip_reason TEXT",
    "ALTER TABLE llm_final_tags ADD COLUMN IF NOT EXISTS skip_reason TEXT",
    "ALTER TABLE llm_final_tags ADD COLUMN IF NOT EXISTS decision VARCHAR(10)",
    "ALTER TABLE llm_final_tags ADD COLUMN IF NOT EXISTS dispute_type VARCHAR(20)",
    "ALTER TABLE llm_final_tags ADD COLUMN IF NOT EXISTS disputed_point TEXT",
    "ALTER TABLE llm_retags ALTER COLUMN first_tag DROP NOT NULL",
]

COMPARISON_VIEW = r"""
CREATE OR REPLACE VIEW llm_tag_comparison AS
WITH tags AS (
    SELECT
        t.source_row_id, t.base_value, t.options,
        regexp_replace(regexp_replace(t.tag, '^\s*others:\s*', '', 'i'),
                       '^\s*<\s*(misc - not a liquor)\s*>\s*$', '\1', 'i') AS tag_1,
        regexp_replace(regexp_replace(r.tag, '^\s*others:\s*', '', 'i'),
                       '^\s*<\s*(misc - not a liquor)\s*>\s*$', '\1', 'i') AS tag_2,
        t.model AS tag_1_model, r.model AS tag_2_model,
        t.is_alcoholic AS is_alcoholic_1, r.is_alcoholic AS is_alcoholic_2,
        r.source_row_id IS NOT NULL AS retagged,
        f.tag AS tag_3, f.model AS tag_3_model, f.is_alcoholic AS is_alcoholic_3,
        t.skip_reason AS skip_reason_1, r.skip_reason AS skip_reason_2, f.skip_reason AS skip_reason_3
    FROM llm_tags t
    LEFT JOIN llm_retags r USING (source_row_id)
    LEFT JOIN llm_final_tags f USING (source_row_id)
)
SELECT
    source_row_id, base_value, options, tag_1, tag_2,
    CASE WHEN retagged THEN COALESCE(lower(trim(tag_1)) = lower(trim(tag_2)), false) END AS tags_match,
    tag_1_model, tag_2_model,
    -- new columns go last: CREATE OR REPLACE VIEW can only add columns at the end
    is_alcoholic_1, is_alcoholic_2,
    is_alcoholic_1 OR is_alcoholic_2 AS is_alcoholic_any,
    tag_3, tag_3_model, is_alcoholic_3,
    skip_reason_1, skip_reason_2, skip_reason_3
FROM tags
"""

VOTES_VIEW = """
CREATE OR REPLACE VIEW llm_tag_votes AS
WITH v AS (
    SELECT c.*,
           lower(trim(tag_3)) = lower(trim(tag_1)) AS tag_3_agrees_1,
           lower(trim(tag_3)) = lower(trim(tag_2)) AS tag_3_agrees_2
    FROM llm_tag_comparison c
)
SELECT
    source_row_id, base_value, options, tag_1, tag_2, tag_3,
    -- a pass 3 result, once there is one, outranks everything except the first two tags matching
    CASE
        WHEN tags_match THEN tag_1
        WHEN tag_3_agrees_1 OR tag_3_agrees_2 THEN tag_3
        WHEN tag_3 IS NOT NULL OR skip_reason_3 IS NOT NULL THEN NULL
        WHEN NOT is_alcoholic_any THEN 'Misc - Not a Liquor'
    END AS final_tag,
    CASE
        WHEN tags_match THEN 'agreed'
        WHEN tag_3_agrees_1 THEN 'final agrees with 1'
        WHEN tag_3_agrees_2 THEN 'final agrees with 2'
        WHEN skip_reason_3 IS NOT NULL THEN 'skipped'
        WHEN tag_3 IS NOT NULL THEN 'no majority'
        WHEN NOT is_alcoholic_any THEN 'both not alcoholic'
        WHEN tags_match IS NULL THEN 'needs retag'
        ELSE 'needs final'
    END AS decision,
    tag_1_model, tag_2_model, tag_3_model,
    CASE WHEN is_alcoholic_1 = is_alcoholic_2 THEN is_alcoholic_1 ELSE is_alcoholic_3 END AS is_alcoholic,
    is_alcoholic_1, is_alcoholic_2, is_alcoholic_3,
    -- why the row has no tag: pass 3 couldn't settle it, or pass 1 skipped it and passes 2 and 3 haven't settled it
    CASE
        WHEN tags_match THEN NULL
        WHEN tag_3_agrees_1 OR tag_3_agrees_2 THEN NULL
        ELSE COALESCE(skip_reason_3, skip_reason_1)
    END AS skip_reason
FROM v
"""

# Passes 1 and 2 side by side, and the input to pass 3. is_match is empty until both passes have given a result; a pass 2
# skip counts as not matching. Pass 3 takes every row whose tags aren't a match, once pass 2 has tried it.
FIRST_SECOND_VIEW = """
CREATE OR REPLACE VIEW first_second_tag_comparison AS
WITH m AS (
    SELECT c.source_row_id, c.base_value, c.options, c.tag_1, c.tag_2,
        CASE
            WHEN c.tag_1 IS NULL THEN NULL
            WHEN c.tag_2 IS NOT NULL THEN lower(trim(c.tag_1)) = lower(trim(c.tag_2))
            WHEN c.skip_reason_2 IS NOT NULL THEN false
        END AS is_match,
        c.is_alcoholic_1, c.is_alcoholic_2, c.is_alcoholic_any,
        c.tag_1_model, c.tag_2_model,
        t.selected_option AS selected_option_1,
        t.suggested_name AS suggested_name_1, r.suggested_name AS suggested_name_2,
        t.confidence AS confidence_1, r.confidence AS confidence_2,
        t.reason AS reason_1, r.reason AS reason_2,
        c.skip_reason_1, c.skip_reason_2,
        t.error AS error_1, r.error AS error_2,
        t.judgements AS judgements_1,
        t.sources AS sources_1, r.sources AS sources_2,
        t.source_insert_dttm,
        t.tagged_at AS tagged_at_1, r.tagged_at AS tagged_at_2
    FROM llm_tag_comparison c
    JOIN llm_tags t USING (source_row_id)
    LEFT JOIN llm_retags r USING (source_row_id)
)
SELECT m.*, is_match IS NOT TRUE AND tagged_at_2 IS NOT NULL AS needs_third_tag
FROM m
"""

# One row per item: all three tags, the final tag, why it's final (or why there isn't one), and passes 1 and 2's sources.
FINAL_SUMMARY_VIEW = """
CREATE OR REPLACE VIEW final_tag_summary AS
SELECT
    v.source_row_id, v.base_value,
    v.tag_1 AS first_tag, v.tag_2 AS second_tag, v.tag_3 AS third_tag, v.final_tag,
    CASE v.decision
        WHEN 'agreed' THEN 'First and second tags match. ' || COALESCE(m.reason_2, '')
        WHEN 'both not alcoholic' THEN 'Neither pass says it is alcoholic. ' || COALESCE(m.reason_1, m.reason_2, '')
        WHEN 'final agrees with 1' THEN 'Third tag agrees with the first tag. ' || COALESCE(f.reason, '')
        WHEN 'final agrees with 2' THEN 'Third tag agrees with the second tag. ' || COALESCE(f.reason, '')
        WHEN 'skipped' THEN 'No final tag, skipped. ' || COALESCE(v.skip_reason, '')
        WHEN 'needs retag' THEN 'No final tag yet, the second pass has no result. ' || COALESCE(m.error_2, '')
        WHEN 'needs final' THEN 'No final tag yet, the third pass has no result. ' || COALESCE(f.error, '')
        WHEN 'no majority' THEN 'No final tag, the third tag agrees with neither of the first two. '
            || COALESCE(f.reason, '')
    END AS final_tag_reason,
    m.sources_1 AS first_tag_sources, m.sources_2 AS second_tag_sources,
    m.tagged_at_1 AS first_tagged_at
FROM llm_tag_votes v
JOIN first_second_tag_comparison m USING (source_row_id)
LEFT JOIN llm_final_tags f USING (source_row_id)
"""

# The human tag from an uploaded CSV next to the LLM's tag (the vote's final tag, else pass 1's).
HUMAN_COMPARISON_VIEW = r"""
CREATE OR REPLACE VIEW human_tag_comparison AS
WITH h AS (
    SELECT r.source_row_id, r.base_value, r.options, r.source_file,
           NULLIF(trim(COALESCE(NULLIF(r.data->>'final_tags', ''), r.data->>'tag_1')), '') AS human_tag,
           COALESCE(v.final_tag, v.tag_1) AS llm_tag,
           v.tag_1_model AS llm_model, v.decision, v.skip_reason
    FROM csv_records r
    LEFT JOIN llm_tag_votes v USING (source_row_id)
), n AS (
    SELECT h.*,
           -- compare ignoring "Others: ", case, punctuation and spacing
           regexp_replace(lower(regexp_replace(human_tag, '^\s*others:\s*', '', 'i')), '[^0-9a-z]+', '', 'g') AS human_key,
           regexp_replace(lower(llm_tag), '[^0-9a-z]+', '', 'g') AS llm_key,
           NOT EXISTS (SELECT 1 FROM unnest(string_to_array(options, '|')) o
                       WHERE lower(trim(o)) = lower(llm_tag)) AS llm_new_name
    FROM h
)
SELECT source_row_id, base_value, options, source_file, human_tag, llm_tag, llm_model, decision, skip_reason,
    CASE
        WHEN human_tag IS NULL THEN 'no human tag'
        WHEN decision = 'skipped' THEN 'llm skipped'
        WHEN llm_tag IS NULL THEN 'not tagged by llm'
        WHEN human_key = llm_key THEN 'same'
        WHEN human_tag ~* '^\s*none\s*$' THEN 'human skipped'
        -- both made up a name: the human's often has a description after it, so check these by eye
        WHEN human_tag ~* '^\s*others:' AND llm_new_name AND lower(llm_tag) <> lower('Misc - Not a Liquor')
            THEN 'both new name'
        ELSE 'different'
    END AS comparison
FROM n
"""

# The old claude_tags, the new llm_tags and the human tag from an uploaded CSV, side by side, for every record in any
# of them. Only created when claude_tags exists, since it's no longer in `metadata`.
# The pipeline's answer per pass 1 row. Pass 2 checks rows pass 1 called Misc or left blank. A row is Misc when
# passes 1 and 2 both say so; every other row is named by pass 3, which doesn't see passes 1 and 2.
FINAL_RESULTS_VIEW = r"""
CREATE OR REPLACE VIEW final_results AS
WITH s AS (
    SELECT
        t.source_row_id, t.base_value, t.options,
        t.tag AS tag_1, r.tag AS tag_2, f.tag AS tag_3,
        t.model AS model_1, r.model AS model_2, f.model AS model_3,
        coalesce(lower(btrim(t.tag, '<> ')) = 'misc - not a liquor', false) AS misc_1,
        coalesce(lower(btrim(r.tag, '<> ')) = 'misc - not a liquor', false) AS misc_2,
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
        NOT misc_1 OR (has_2 AND NOT misc_2) AS needs_pass_3
    FROM (SELECT *, tag_1 IS NULL OR misc_1 AS t_blank_or_misc FROM s) x
)
SELECT
    source_row_id, base_value, options, tag_1, tag_2, tag_3,
    CASE WHEN misc_1 AND misc_2 THEN 'Misc - Not a Liquor'
         WHEN needs_pass_3 THEN tag_3 END AS final_tag,
    CASE WHEN misc_1 AND misc_2 THEN 'misc: passes 1 and 2 agree'
         WHEN misc_1 AND NOT has_2 THEN 'waiting for pass 2'
         WHEN NOT has_3 THEN 'waiting for pass 3'
         WHEN tag_3 IS NOT NULL THEN 'named by pass 3'
         WHEN skip_reason_3 IS NOT NULL THEN 'pass 3 skipped'
         ELSE 'pass 3 failed' END AS status,
    CASE WHEN misc_1 AND misc_2 THEN false WHEN needs_pass_3 THEN is_alcoholic_3 END AS is_alcoholic,
    CASE WHEN misc_1 AND misc_2 THEN reason_2
         WHEN needs_pass_3 THEN coalesce(reason_3, skip_reason_3, error_3) END AS reason,
    needs_pass_2, needs_pass_3, model_1, model_2, model_3
FROM routed
"""

THREE_WAY_COMPARISON_VIEW = r"""
CREATE OR REPLACE VIEW three_way_tag_comparison AS
WITH ids AS (
    SELECT source_row_id FROM csv_records
    UNION SELECT source_row_id FROM claude_tags
    UNION SELECT source_row_id FROM llm_tags
), j AS (
    SELECT i.source_row_id,
           COALESCE(h.base_value, c.base_value, l.base_value) AS base_value,
           COALESCE(h.options, array_to_string(COALESCE(l.options, c.options), ' | ')) AS options,
           h.source_file,
           h.source_row_id IS NOT NULL AS in_csv,
           NULLIF(trim(COALESCE(NULLIF(h.data->>'final_tags', ''), h.data->>'tag_1')), '') AS human_tag,
           c.source_row_id IS NOT NULL AS in_claude, c.tag AS claude_tag, c.model AS claude_model,
           c.skip_reason AS claude_skip_reason, c.error AS claude_error,
           l.source_row_id IS NOT NULL AS in_llm, l.tag AS llm_tag, l.model AS llm_model,
           l.skip_reason AS llm_skip_reason, l.error AS llm_error
    FROM ids i
    LEFT JOIN csv_records h USING (source_row_id)
    LEFT JOIN claude_tags c USING (source_row_id)
    LEFT JOIN llm_tags l USING (source_row_id)
), n AS (
    -- compare ignoring "Others: ", case, punctuation and spacing (also turns "<Misc - not a Liquor>" into the plain form)
    SELECT j.*,
           regexp_replace(lower(regexp_replace(human_tag, '^\s*others:\s*', '', 'i')), '[^0-9a-z]+', '', 'g') AS human_key,
           regexp_replace(lower(regexp_replace(claude_tag, '^\s*others:\s*', '', 'i')), '[^0-9a-z]+', '', 'g') AS claude_key,
           regexp_replace(lower(regexp_replace(llm_tag, '^\s*others:\s*', '', 'i')), '[^0-9a-z]+', '', 'g') AS llm_key
    FROM j
)
SELECT source_row_id, base_value, options, source_file, human_tag, claude_tag, llm_tag, claude_model, llm_model,
    claude_skip_reason, llm_skip_reason, claude_error, llm_error,
    CASE
        WHEN NOT in_claude THEN 'not in claude_tags'
        WHEN NOT in_llm THEN 'not in llm_tags'
        WHEN claude_error IS NOT NULL OR llm_error IS NOT NULL THEN 'error'
        WHEN claude_tag IS NULL AND llm_tag IS NULL THEN 'both skipped'
        WHEN claude_tag IS NULL THEN 'claude skipped'
        WHEN llm_tag IS NULL THEN 'llm skipped'
        WHEN claude_key = llm_key THEN 'same'
        ELSE 'different'
    END AS claude_vs_llm,
    CASE
        WHEN human_tag IS NULL THEN 'no human tag'
        WHEN NOT in_claude THEN 'not in claude_tags'
        WHEN claude_error IS NOT NULL THEN 'error'
        WHEN claude_tag IS NULL THEN 'claude skipped'
        WHEN human_key = claude_key THEN 'same'
        WHEN human_tag ~* '^\s*none\s*$' THEN 'human skipped'
        ELSE 'different'
    END AS human_vs_claude,
    CASE
        WHEN human_tag IS NULL THEN 'no human tag'
        WHEN NOT in_llm THEN 'not in llm_tags'
        WHEN llm_error IS NOT NULL THEN 'error'
        WHEN llm_tag IS NULL THEN 'llm skipped'
        WHEN human_key = llm_key THEN 'same'
        WHEN human_tag ~* '^\s*none\s*$' THEN 'human skipped'
        ELSE 'different'
    END AS human_vs_llm,
    CASE
        WHEN human_tag IS NULL OR claude_tag IS NULL OR llm_tag IS NULL THEN 'not all tagged'
        WHEN human_tag ~* '^\s*none\s*$' THEN 'human skipped'
        WHEN claude_key = llm_key AND llm_key = human_key THEN 'all same'
        WHEN claude_key = llm_key THEN 'human differs'
        WHEN claude_key = human_key THEN 'llm differs'
        WHEN llm_key = human_key THEN 'claude differs'
        ELSE 'all different'
    END AS claude_vs_llm_vs_human
FROM n
"""


VIEWS = [COMPARISON_VIEW, FIRST_SECOND_VIEW, VOTES_VIEW, FINAL_SUMMARY_VIEW, HUMAN_COMPARISON_VIEW, FINAL_RESULTS_VIEW]
VIEW_NAMES = ["three_way_tag_comparison", "final_results", "human_tag_comparison", "final_tag_summary",
              "llm_tag_votes", "first_second_tag_comparison", "llm_tag_comparison"]  # dependents first


def _now():
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "topk_options",
        sa.Column("source_row_id", sa.String, primary_key=True),
        sa.Column("base_value", sa.Text),
        sa.Column("model", sa.Boolean),
        sa.Column("options", sa.Text),
        sa.Column("insert_dttm", sa.DateTime(timezone=True)),
        sa.Column("loaded_at", sa.DateTime(timezone=True), server_default=_now()),
        if_not_exists=True,
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now()),
        if_not_exists=True,
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        if_not_exists=True,
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("command", sa.String(20), nullable=False),
        sa.Column("argv", JSONB, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("exit_code", sa.Integer),
        sa.Column("started_by", sa.String(255)),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=_now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        if_not_exists=True,
    )
    op.create_table(
        "llm_tags",
        sa.Column("source_row_id", sa.String, primary_key=True),
        sa.Column("base_value", sa.Text, nullable=False),
        sa.Column("options", ARRAY(sa.String), nullable=False),
        sa.Column("tag", sa.Text),
        sa.Column("selected_option", sa.Text),
        sa.Column("is_alcoholic", sa.Boolean),
        sa.Column("suggested_name", sa.Text),
        sa.Column("confidence", sa.String(10)),
        sa.Column("reason", sa.Text),
        sa.Column("judgements", JSONB, nullable=False, server_default="[]"),
        sa.Column("sources", JSONB, nullable=False, server_default="[]"),
        sa.Column("error", sa.Text),
        sa.Column("skip_reason", sa.Text),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("source_insert_dttm", sa.DateTime(timezone=True)),
        sa.Column("tagged_at", sa.DateTime(timezone=True), server_default=_now()),
        if_not_exists=True,
    )
    op.create_table(
        "llm_retags",
        sa.Column("source_row_id", sa.String, primary_key=True),
        sa.Column("base_value", sa.Text, nullable=False),
        sa.Column("first_tag", sa.Text),
        sa.Column("first_model", sa.String(64)),
        sa.Column("tag", sa.Text),
        sa.Column("is_alcoholic", sa.Boolean),
        sa.Column("suggested_name", sa.Text),
        sa.Column("confidence", sa.String(10)),
        sa.Column("reason", sa.Text),
        sa.Column("sources", JSONB, nullable=False, server_default="[]"),
        sa.Column("error", sa.Text),
        sa.Column("skip_reason", sa.Text),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("tagged_at", sa.DateTime(timezone=True), server_default=_now()),
        if_not_exists=True,
    )
    op.create_table(
        "llm_final_tags",
        sa.Column("source_row_id", sa.String, primary_key=True),
        sa.Column("base_value", sa.Text, nullable=False),
        sa.Column("options", ARRAY(sa.String), nullable=False),
        sa.Column("tag_1", sa.Text),
        sa.Column("tag_2", sa.Text),
        sa.Column("tag_1_model", sa.String(64)),
        sa.Column("tag_2_model", sa.String(64)),
        sa.Column("tag", sa.Text),
        sa.Column("selected_option", sa.Text),
        sa.Column("is_alcoholic", sa.Boolean),
        sa.Column("suggested_name", sa.Text),
        sa.Column("confidence", sa.String(10)),
        sa.Column("reason", sa.Text),
        sa.Column("judgements", JSONB, nullable=False, server_default="[]"),
        sa.Column("sources", JSONB, nullable=False, server_default="[]"),
        sa.Column("error", sa.Text),
        sa.Column("skip_reason", sa.Text),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("tagged_at", sa.DateTime(timezone=True), server_default=_now()),
        sa.Column("decision", sa.String(10)),
        sa.Column("dispute_type", sa.String(20)),
        sa.Column("disputed_point", sa.Text),
        if_not_exists=True,
    )
    op.create_table(
        "csv_records",
        sa.Column("source_row_id", sa.String, primary_key=True),
        sa.Column("base_value", sa.Text, nullable=False),
        sa.Column("options", sa.Text),
        sa.Column("source_file", sa.Text, nullable=False),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("tagged_at", sa.DateTime(timezone=True), server_default=_now()),
        if_not_exists=True,
    )
    for statement in MIGRATIONS + VIEWS:
        op.execute(statement)
    # the old claude_tags table only exists in databases from before the llm_* rename
    if op.get_bind().exec_driver_sql("SELECT to_regclass('claude_tags')").scalar():
        op.execute(THREE_WAY_COMPARISON_VIEW)


def downgrade() -> None:
    """Drops everything this revision made, data included."""
    for name in VIEW_NAMES:
        op.execute(f"DROP VIEW IF EXISTS {name}")
    for name in ("csv_records", "llm_final_tags", "llm_retags", "llm_tags", "jobs", "sessions", "users",
                 "topk_options"):
        op.drop_table(name, if_exists=True)
