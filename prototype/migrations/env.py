"""Alembic environment. The service passes a live connection via config.attributes["connection"]."""

from alembic import context
from sqlalchemy import create_engine

from prototype.storage import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:  # CLI use: alembic -x url=... upgrade head
        url = context.get_x_argument(as_dictionary=True).get("url")
        if not url:
            raise RuntimeError("pass -x url=<database url> or run through the service")
        with create_engine(url).connect() as conn:
            _run(conn)
    else:
        _run(connection)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
