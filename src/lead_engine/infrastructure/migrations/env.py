"""Use the same engine configuration for CLI and Alembic."""

from alembic import context

from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.orm import Base

config = context.config


def run() -> None:
    if context.is_offline_mode():
        context.configure(
            url=config.get_main_option("sqlalchemy.url"),
            target_metadata=Base.metadata,
            literal_binds=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    provided = config.attributes.get("connection")
    if provided is not None:
        context.configure(connection=provided, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = build_engine(config.get_main_option("sqlalchemy.url") or "sqlite://")
    try:
        with engine.begin() as connection:
            context.configure(
                connection=connection, target_metadata=Base.metadata, compare_type=True
            )
            context.run_migrations()
    finally:
        engine.dispose()


run()
