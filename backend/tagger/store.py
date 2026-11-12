"""Postgres: the source of truth for every pass."""
import re
from pathlib import Path

from sqlalchemy import (ARRAY, Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, create_engine,
                        func, select, text)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from .config import ROOT
from .source import SourceRow, clean_row
from .claude_tagger import TagResult

metadata = MetaData()

# The Athena topk_options table, copied whole by `refresh`. `run` reads candidates from here.
topk_options = Table(
    "topk_options", metadata,
    Column("source_row_id", String, primary_key=True),
    Column("base_value", Text),
    Column("model", Boolean),
    Column("options", Text),  # as in Athena: "a|b|c"
    Column("insert_dttm", DateTime(timezone=True)),
    Column("loaded_at", DateTime(timezone=True), server_default=func.now()),
)

# Web app accounts and their sign-ins. A session row is revoked on logout, which is what makes a JWT stop working.
users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(255), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("first_name", String(100), nullable=False, server_default=""),
    Column("last_name", String(100), nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

sessions = Table(
    "sessions", metadata,
    Column("id", String(36), primary_key=True),  # the JWT's jti
    Column("user_id", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("revoked_at", DateTime(timezone=True)),
)

# Every command started from the web app; its log is the file data/logs/<id>.log.
jobs = Table(
    "jobs", metadata,
    Column("id", String(36), primary_key=True),
    Column("command", String(20), nullable=False),
    Column("argv", JSONB, nullable=False),
    Column("status", String(10), nullable=False),  # running | done | failed | stopped | lost
    Column("exit_code", Integer),
    Column("started_by", String(255)),
    Column("started_at", DateTime(timezone=True), server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
)

llm_tags = Table(
    "llm_tags", metadata,
    Column("source_row_id", String, primary_key=True),
    Column("base_value", Text, nullable=False),
    Column("options", ARRAY(String), nullable=False),
    Column("tag", Text),
    Column("selected_option", Text),
    Column("is_alcoholic", Boolean),
    Column("suggested_name", Text),
    Column("confidence", String(10)),
    Column("reason", Text),
    Column("judgements", JSONB, nullable=False, server_default="[]"),
    Column("sources", JSONB, nullable=False, server_default="[]"),
    Column("error", Text),
    Column("skip_reason", Text),  # set when the tagger found the record too unclear; tag is then NULL
    Column("model", String(64), nullable=False),
    Column("source_insert_dttm", DateTime(timezone=True)),
    Column("tagged_at", DateTime(timezone=True), server_default=func.now()),
)

llm_retags = Table(
    "llm_retags", metadata,
    Column("source_row_id", String, primary_key=True),
    Column("base_value", Text, nullable=False),
    Column("first_tag", Text),  # NULL when pass 1 skipped or failed
    Column("first_model", String(64)),
    Column("tag", Text),
    Column("is_alcoholic", Boolean),
    Column("suggested_name", Text),
    Column("confidence", String(10)),
    Column("reason", Text),
    Column("sources", JSONB, nullable=False, server_default="[]"),
    Column("error", Text),
    Column("skip_reason", Text),  # set when the tagger found the record too unclear; tag is then NULL
    Column("model", String(64), nullable=False),
    Column("tagged_at", DateTime(timezone=True), server_default=func.now()),
)

llm_final_tags = Table(
    "llm_final_tags", metadata,
    Column("source_row_id", String, primary_key=True),
    Column("base_value", Text, nullable=False),
    Column("options", ARRAY(String), nullable=False),
    Column("tag_1", Text),
    Column("tag_2", Text),
    Column("tag_1_model", String(64)),
    Column("tag_2_model", String(64)),
    Column("tag", Text),
    Column("selected_option", Text),
    Column("is_alcoholic", Boolean),
    Column("suggested_name", Text),
    Column("confidence", String(10)),
    Column("reason", Text),
    Column("judgements", JSONB, nullable=False, server_default="[]"),
    Column("sources", JSONB, nullable=False, server_default="[]"),
    Column("error", Text),
    Column("skip_reason", Text),  # set when the tagger found the record too unclear; tag is then NULL
    Column("model", String(64), nullable=False),
    Column("tagged_at", DateTime(timezone=True), server_default=func.now()),
    Column("decision", String(10)),  # MODEL_1 | MODEL_2 | HUMAN; a HUMAN row is saved as a skip
    Column("dispute_type", String(20)),
    Column("disputed_point", Text),
)

# Every row of each CSV given to `run --csv` or `import`, kept as uploaded.
csv_records = Table(
    "csv_records", metadata,
    Column("source_row_id", String, primary_key=True),
    Column("base_value", Text, nullable=False),
    Column("options", Text),          # as in the file: "a | b | c"
    Column("source_file", Text, nullable=False),
    Column("data", JSONB, nullable=False),  # the whole row, including human tags such as tag_1 and final_tags
    Column("tagged_at", DateTime(timezone=True), server_default=func.now()),  # when it was uploaded
)

def _upsert(conn, table: Table, values: dict | list[dict]) -> None:
    statement = insert(table).values(values)
    updates = {c.name: statement.excluded[c.name] for c in table.columns
               if c.name not in ("source_row_id", "tagged_at")}
    updates["tagged_at"] = func.now()
    conn.execute(statement.on_conflict_do_update(index_elements=["source_row_id"], set_=updates))


def _decision_values(result: TagResult) -> dict:
    return {
        "tag": result.tag, "is_alcoholic": result.is_alcoholic, "suggested_name": result.suggested_name,
        "confidence": result.confidence, "reason": result.reason, "sources": result.sources or [],
        "error": result.error, "skip_reason": result.skip_reason,
    }


CONNECT_ARGS = {"connect_timeout": 10}


def _create_database(database_url: str) -> None:
    """Create the database in DATABASE_URL, connecting to the server's built-in `postgres` database to do it."""
    url = make_url(database_url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", connect_args=CONNECT_ARGS)
    try:
        with admin.connect() as conn:
            # template0 allows no connections, so creating can't fail with "template1 is being accessed by other users"
            name = conn.dialect.identifier_preparer.quote(url.database)
            conn.exec_driver_sql(f"CREATE DATABASE {name} TEMPLATE template0")
    finally:
        admin.dispose()
    print(f"Created database {url.database}", flush=True)


def migrate(database_url: str) -> None:
    """Bring the database to the latest Alembic revision, creating the database itself if it doesn't exist.
    The API runs this once at startup; `python -m tagger migrate` runs it by hand."""
    from alembic import command
    from alembic.config import Config

    engine = create_engine(database_url, connect_args=CONNECT_ARGS)
    try:
        with engine.connect():
            pass
    except OperationalError as e:
        if f'database "{make_url(database_url).database}" does not exist' not in str(e.orig):
            raise
        _create_database(database_url)
    finally:
        engine.dispose()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))  # ini values treat % as interpolation
    config.attributes["embedded"] = True  # env.py then leaves the caller's logging alone
    command.upgrade(config, "head")


class Store:
    """Reads and writes the tables. The schema itself belongs to the migrations in migrations/versions/."""

    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, pool_pre_ping=True, connect_args=CONNECT_ARGS)

    def schema_ready(self) -> bool:
        """Whether migrations have run on this database (connects, so it also surfaces connection errors)."""
        with self.engine.connect() as conn:
            return bool(conn.exec_driver_sql("SELECT to_regclass('alembic_version')").scalar())

    def _rows(self, statement) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(statement)]

    def _ids(self, table: Table, include_errors: bool) -> set[str]:
        statement = select(table.c.source_row_id)
        if not include_errors:
            statement = statement.where(table.c.error.is_(None))
        with self.engine.connect() as conn:
            return set(conn.execute(statement).scalars())

    # Source rows (the local copy of Athena's topk_options)

    def replace_source(self, rows, batch: int = 2000) -> int:
        """Replace every row of topk_options with `rows` (dicts from fetch_athena), in one transaction, so a failed
        refresh leaves the old copy in place. Returns the number of rows loaded."""
        count = 0
        with self.engine.begin() as conn:
            conn.execute(topk_options.delete())
            chunk = []
            for r in rows:
                if not r.get("source_row_id"):
                    continue
                chunk.append(r)
                if len(chunk) >= batch:
                    count += self._insert_source(conn, chunk)
                    chunk = []
            if chunk:
                count += self._insert_source(conn, chunk)
        return count

    @staticmethod
    def _insert_source(conn, chunk: list[dict]) -> int:
        by_id = {r["source_row_id"]: r for r in chunk}  # one statement can't upsert the same id twice
        statement = insert(topk_options).values(list(by_id.values()))
        updates = {c.name: statement.excluded[c.name] for c in topk_options.columns if c.name != "source_row_id"}
        conn.execute(statement.on_conflict_do_update(index_elements=["source_row_id"], set_=updates))
        return len(by_id)

    def source_rows(self, watermark=None, stats=None):
        """Cleaned SourceRows from topk_options, oldest first, from the watermark on (all rows when None)."""
        statement = select(topk_options).order_by(topk_options.c.insert_dttm.asc().nulls_first(),
                                                  topk_options.c.source_row_id)
        if watermark is not None:
            statement = statement.where(topk_options.c.insert_dttm >= watermark)
        with self.engine.connect() as conn:
            for r in conn.execute(statement.execution_options(yield_per=5000)):
                if stats is not None:
                    stats.fetched += 1
                row = clean_row(r.source_row_id, r.base_value, r.options, r.insert_dttm)
                if row is None:
                    if stats is not None:
                        stats.invalid += 1
                        stats.invalid_ids.append(str(r.source_row_id))
                    continue
                yield row

    def source_stats(self) -> dict:
        statement = select(func.count(), func.min(topk_options.c.insert_dttm), func.max(topk_options.c.insert_dttm),
                           func.max(topk_options.c.loaded_at)).select_from(topk_options)
        with self.engine.connect() as conn:
            rows, oldest, newest, loaded_at = conn.execute(statement).one()
        return {"rows": rows, "oldest_insert_dttm": oldest, "newest_insert_dttm": newest, "loaded_at": loaded_at}

    # Pass 1

    def watermark(self):
        with self.engine.connect() as conn:
            return conn.execute(select(func.max(llm_tags.c.source_insert_dttm))).scalar()

    def tagged_ids(self, include_errors: bool = True, table: Table = llm_tags) -> set[str]:
        return self._ids(table, include_errors)

    def tags_table(self, name: str, like: Table = llm_tags) -> Table:
        """A table shaped like `like` (llm_tags by default), created if missing, for rerunning a pass elsewhere."""
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) or name in metadata.tables:
            raise ValueError(f"{name!r}: use lowercase letters, digits and _, and not one of {sorted(metadata.tables)}")
        table = like.to_metadata(MetaData(), name=name)
        table.create(self.engine, checkfirst=True)
        return table

    def save(self, row: SourceRow, result: TagResult, model: str, table: Table = llm_tags) -> None:
        options = list(row.options)
        # A suggested name becomes one more option, so the tag always reads like a candidate.
        if result.tag and result.tag == result.suggested_name and result.tag not in options:
            options.append(result.tag)
        values = {"source_row_id": row.source_row_id, "base_value": row.base_value, "options": options,
                  "selected_option": result.selected_option, "judgements": result.judgements or [],
                  "model": model, "source_insert_dttm": row.insert_dttm, **_decision_values(result)}
        with self.engine.begin() as conn:
            _upsert(conn, table, values)

    def error_rows(self) -> list[dict]:
        """Failed pass 1 rows, oldest first, as stored (including human edits)."""
        statement = (select(llm_tags).where(llm_tags.c.error.is_not(None))
                     .order_by(llm_tags.c.source_insert_dttm.asc().nulls_first(), llm_tags.c.source_row_id))
        return self._rows(statement)

    def first_pass_rows(self, table: Table = llm_tags) -> list[dict]:
        statement = select(table).order_by(table.c.source_insert_dttm.asc().nulls_first(), table.c.source_row_id)
        return self._rows(statement)

    # Pass 2

    def retagged_ids(self, include_errors: bool = True, table: Table = llm_retags) -> set[str]:
        return self._ids(table, include_errors)

    def save_retag(self, first: dict, result: TagResult, model: str, table: Table = llm_retags) -> None:
        values = {"source_row_id": first["source_row_id"], "base_value": first["base_value"],
                  "first_tag": first["tag"], "first_model": first["model"], "model": model,
                  **_decision_values(result)}
        with self.engine.begin() as conn:
            _upsert(conn, table, values)

    # Pass 3

    def pass3_rows(self) -> list[dict]:
        """Pass 1 rows that pass 3 must name (final_results.needs_pass_3), with pass 2's tag and model."""
        return self._rows(text(
            "SELECT t.*, v.tag_2, v.model_2 FROM final_results v JOIN llm_tags t USING (source_row_id) "
            "WHERE v.needs_pass_3 ORDER BY t.source_insert_dttm NULLS FIRST, t.source_row_id"))

    def final_tagged_ids(self, include_errors: bool = True) -> set[str]:
        return self._ids(llm_final_tags, include_errors)

    def save_final(self, first: dict, row: SourceRow, result: TagResult, model: str) -> None:
        """`first` is a pass3_rows row. tag_1 and tag_2 record what passes 1 and 2 said, for comparison only."""
        values = {"source_row_id": row.source_row_id, "base_value": row.base_value, "options": row.options,
                  "tag_1": first["tag"], "tag_2": first["tag_2"],
                  "tag_1_model": first["model"], "tag_2_model": first["model_2"],
                  "selected_option": result.selected_option, "judgements": result.judgements or [],
                  "decision": result.decision, "dispute_type": result.dispute_type,
                  "disputed_point": result.disputed_point, "model": model, **_decision_values(result)}
        with self.engine.begin() as conn:
            _upsert(conn, llm_final_tags, values)

    # Uploaded CSVs

    def save_csv(self, path: str, rows: list[dict]) -> int:
        """Upsert the rows read by read_csv into csv_records. Returns how many were saved."""
        by_id = {}  # one statement can't upsert the same id twice, so a later duplicate wins
        for r in rows:
            row_id, base_value = (r.get("source_row_id") or "").strip(), (r.get("base_value") or "").strip()
            if row_id and base_value:
                by_id[row_id] = {"source_row_id": row_id, "base_value": base_value, "options": r.get("options"),
                                 "source_file": Path(path).name, "data": r}
        if by_id:
            with self.engine.begin() as conn:
                _upsert(conn, csv_records, list(by_id.values()))
        return len(by_id)

    # Export

    def all_rows(self) -> list[dict]:
        return self._rows(select(llm_tags).order_by(llm_tags.c.source_row_id))

    def all_retags(self) -> list[dict]:
        return self._rows(select(llm_retags).order_by(llm_retags.c.source_row_id))
