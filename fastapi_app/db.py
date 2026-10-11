"""Small synchronous PostgreSQL access layer used by the HTTP application."""

from contextlib import contextmanager
import os

import psycopg
from psycopg.rows import dict_row


def connection(*, migrator=False):
    return psycopg.connect(
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["DB_MIGRATOR_USER"] if migrator else os.environ["DB_APP_USER"],
        password=os.environ["DB_MIGRATOR_PASSWORD"] if migrator else os.environ["DB_APP_PASSWORD"],
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
        row_factory=dict_row,
    )


@contextmanager
def transaction():
    with connection() as conn:
        with conn.transaction():
            yield conn


def ensure_runtime_schema():
    with connection(migrator=True) as conn:
        with conn.transaction():
            conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_sessions (
                token_hash char(64) PRIMARY KEY,
                user_id uuid NOT NULL REFERENCES accounts_user(id) ON DELETE CASCADE,
                auth_version integer NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                expires_at timestamptz NOT NULL
            )
            """
        )
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app_sessions TO kp11_app")


def one(conn, query, params=()):
    return conn.execute(query, params).fetchone()


def many(conn, query, params=()):
    return conn.execute(query, params).fetchall()
