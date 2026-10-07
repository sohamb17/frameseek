import os

import pytest

needs_db = pytest.mark.skipif(not os.environ.get("FRAMESEEK_TEST_DATABASE_URL"),
                              reason="set FRAMESEEK_TEST_DATABASE_URL to run database tests")


def apply_schema(conn, schema_dir) -> None:
    """Apply the Go API's SQL migrations that are missing, tracked like the API does."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    if not done and conn.execute("SELECT to_regclass('public.segments')").fetchone()[0]:
        done = {"sql/0001_init.sql"}  # created by an older fixture that did not record migrations
        conn.execute("INSERT INTO schema_migrations (name) VALUES ('sql/0001_init.sql')")
    for f in sorted(schema_dir.glob("*.sql")):
        name = f"sql/{f.name}"
        if name not in done:
            conn.execute(f.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
