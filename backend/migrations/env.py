"""Alembic environment. The database URL comes from .env (DATABASE_URL) unless the caller set sqlalchemy.url."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from tagger.config import load_settings
from tagger.store import metadata

config = context.config
# migrate() runs inside the API and the CLI; their logging stays as it is
if config.config_file_name and not config.attributes.get("embedded"):
    fileConfig(config.config_file_name)

url = config.get_main_option("sqlalchemy.url") or load_settings().database_url
if not url:
    raise SystemExit("DATABASE_URL is not set in .env")


def include_name(name, type_, parent_names):
    # Pass 2's on-demand copies (e.g. llm_retags_v2) and the old claude_tags table aren't in the metadata;
    # without this, autogenerate would propose dropping them.
    return type_ != "table" or name in metadata.tables


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=metadata, include_name=include_name, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata, include_name=include_name,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
