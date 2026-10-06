"""Postgres access (psycopg 3 + pgvector)."""
from __future__ import annotations

import contextlib
import time
from typing import Iterator

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

_pool: ConnectionPool | None = None


def _configure(conn: psycopg.Connection) -> None:
    register_vector(conn)


def wait_for_schema(timeout_s: float | None = None) -> None:
    """The Go API owns migrations; Python services wait until they have run.

    Waits indefinitely by default (logging every 30 s) instead of crashing: a crash would
    restart the container and kill anything running inside it via `docker compose exec`.
    """
    start = last_log = time.time()
    while True:
        try:
            with psycopg.connect(settings().database_url, connect_timeout=5) as conn:
                row = conn.execute("SELECT to_regclass('public.segments')").fetchone()
                if row and row[0]:
                    return
        except psycopg.OperationalError:
            pass
        if timeout_s is not None and time.time() - start > timeout_s:
            raise RuntimeError("database schema not ready (is the api container running migrations?)")
        if time.time() - last_log > 30:
            print("waiting for the database schema (the api container applies migrations; check `docker compose logs api`)",
                  flush=True)
            last_log = time.time()
        time.sleep(2)


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        wait_for_schema()
        _pool = ConnectionPool(
            settings().database_url,
            min_size=1,
            max_size=8,
            configure=_configure,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


@contextlib.contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with pool().connection() as conn:
        yield conn
