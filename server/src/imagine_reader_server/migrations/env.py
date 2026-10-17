"""Alembic environment.

The server applies migrations on startup (db.upgrade_database) and passes its
connection in. From the command line, the database comes from Settings:

    .venv/bin/alembic -c server/alembic.ini revision --autogenerate -m "describe the change"
"""

from alembic import context
from sqlalchemy import create_engine

from imagine_reader_server.config import Settings
from imagine_reader_server.db import Base


def run(connection) -> None:
    # render_as_batch: SQLite cannot alter most columns in place, so changes copy the table.
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with create_engine(settings.database_url).begin() as cli_connection:
        run(cli_connection)
