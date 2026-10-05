"""Local schema administration; initialization always applies migrations."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine
from sqlalchemy.engine import make_url

from lead_engine.infrastructure.database import build_engine


def migration_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def upgrade_database(url: str) -> None:
    engine = build_engine(url)
    try:
        with engine.begin() as connection:
            config = migration_config(url)
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def database_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def latest_revision() -> str | None:
    return ScriptDirectory.from_config(migration_config("sqlite://")).get_current_head()


def local_database_path(url: str) -> Path:
    parsed = make_url(url)
    if (
        parsed.get_backend_name() != "sqlite"
        or not parsed.database
        or parsed.database == ":memory:"
    ):
        raise ValueError("Reset supports only file-backed local SQLite databases")
    if parsed.query or parsed.host:
        raise ValueError("Reset does not support SQLite URI/query options")
    original_path = Path(parsed.database)
    if original_path.is_symlink():
        raise ValueError("Refusing to reset a symbolic link")
    path = original_path.resolve()
    workspace = Path.cwd().resolve()
    if not path.is_relative_to(workspace) or path == workspace:
        raise ValueError("Database must be inside the current working directory")
    if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("Database must have a .db, .sqlite or .sqlite3 extension")
    if path.exists():
        with path.open("rb") as handle:
            if handle.read(16) != b"SQLite format 3\x00":
                raise ValueError("Refusing to reset a file without a SQLite header")
    return path


def reset_local_database(url: str) -> None:
    path = local_database_path(url)
    # Require other clients to be closed; include SQLite journal files after path validation.
    for suffix in ("-wal", "-shm", "-journal", ""):
        candidate = Path(str(path) + suffix)
        if candidate.is_symlink():
            raise ValueError("Refusing to delete a symbolic link")
    for suffix in ("-wal", "-shm", "-journal", ""):
        Path(str(path) + suffix).unlink(missing_ok=True)
    upgrade_database(url)
