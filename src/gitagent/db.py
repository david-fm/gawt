"""SQLite state store for gitagent v0.6.0.

Provides a single-file database with PRAGMA user_version-based migrations.
All gitagent state (sessions, agents, intents, edits, snapshot progress,
locks, snapshots) lives here. The inbox is gone: coordination emerges from
the edit log (pheromone), not from messages.

Thread-safe: each thread gets its own SQLite connection via threading.local().
This allows MCP subagents running in separate threads to safely share the
same Database instance without "SQLite objects created in a thread can only
be used in that same thread" errors.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

CURRENT_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id              TEXT PRIMARY KEY,
    feature         TEXT NOT NULL,
    target_branch   TEXT NOT NULL,
    base_sha        TEXT NOT NULL,
    worktree        TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'open',
    created_at      TEXT NOT NULL,
    ended_at        TEXT,
    final_sha       TEXT,
    lock_ttl_seconds INTEGER NOT NULL DEFAULT 15
);

CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES session(id),
    role        TEXT NOT NULL DEFAULT '',
    started_at  TEXT NOT NULL,
    ended_at    TEXT
);

CREATE TABLE IF NOT EXISTS intents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    intent      TEXT NOT NULL,
    ts          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id     TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    file         TEXT NOT NULL,
    op           TEXT NOT NULL,
    old_string   TEXT,
    new_string   TEXT,
    full_content TEXT,
    intent_id    INTEGER,
    replace_all  INTEGER NOT NULL DEFAULT 0,
    ts           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edits_file ON edits(session_id, file, ts);

CREATE TABLE IF NOT EXISTS snapshot_progress (
    session_id   TEXT NOT NULL,
    file         TEXT NOT NULL,
    last_edit_id INTEGER NOT NULL DEFAULT 0,
    last_ts      TEXT,
    PRIMARY KEY (session_id, file)
);

CREATE TABLE IF NOT EXISTS locks (
    file         TEXT PRIMARY KEY,
    holder_agent TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    token        TEXT NOT NULL,
    acquired_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL,
    message          TEXT NOT NULL,
    boundary_edit_id INTEGER,
    files            TEXT NOT NULL,
    sha              TEXT NOT NULL,
    ts               TEXT NOT NULL
);
"""

_COLUMN_EXISTS = (
    "SELECT COUNT(*) FROM pragma_table_info(?) WHERE name = ?"
)


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply schema migrations using PRAGMA user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= CURRENT_VERSION:
        return

    if version == 0:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
        conn.commit()
        return

    # version == 1 || version == 2: migrate up to v3 incrementally.
    conn.execute("DROP INDEX IF EXISTS idx_one_open_session")
    conn.execute("DROP INDEX IF EXISTS idx_inbox_to")
    conn.execute("DROP TABLE IF EXISTS inbox")
    if conn.execute(_COLUMN_EXISTS, ("session", "lock_ttl_seconds")).fetchone()[0] == 0:
        conn.execute(
            "ALTER TABLE session ADD COLUMN lock_ttl_seconds "
            "INTEGER NOT NULL DEFAULT 15"
        )
    if conn.execute(_COLUMN_EXISTS, ("edits", "replace_all")).fetchone()[0] == 0:
        conn.execute(
            "ALTER TABLE edits ADD COLUMN replace_all INTEGER NOT NULL DEFAULT 0"
        )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS snapshot_progress (
            session_id   TEXT NOT NULL,
            file         TEXT NOT NULL,
            last_edit_id INTEGER NOT NULL DEFAULT 0,
            last_ts      TEXT,
            PRIMARY KEY (session_id, file)
        );
        CREATE TABLE IF NOT EXISTS locks (
            file         TEXT PRIMARY KEY,
            holder_agent TEXT NOT NULL,
            session_id   TEXT NOT NULL,
            token        TEXT NOT NULL,
            acquired_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id       TEXT NOT NULL,
            message          TEXT NOT NULL,
            boundary_edit_id INTEGER,
            files            TEXT NOT NULL,
            sha              TEXT NOT NULL,
            ts               TEXT NOT NULL
        );
        """
    )
    conn.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
    conn.commit()


class Database:
    """Thin wrapper around a sqlite3 connection with migration support.

    Each thread gets its own connection via threading.local() to avoid
    SQLite threading errors when MCP subagents run in separate threads.
    A lock protects initial connection creation to prevent WAL init races.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._local = threading.local()
        self._init_lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        """Return the connection for the current thread, creating if needed."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            return self._local.conn
        with self._init_lock:
            if hasattr(self._local, "conn") and self._local.conn is not None:
                return self._local.conn
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            _migrate(conn)
            self._local.conn = conn
            return self._local.conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._get_conn()

    def close(self) -> None:
        """Close the connection for the current thread."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    def close_all(self) -> None:
        """Close the current thread's connection. For full cleanup, call reset_db()."""
        self.close()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params: list[tuple]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, params)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()


_db: Database | None = None


def get_db(path: Path | None = None) -> Database:
    """Return a singleton Database instance.

    If *path* is None, defaults to ``.gitagent/state.db`` relative to the
    current git repo root.
    """
    global _db
    if _db is None:
        if path is None:
            from .gitwrap import repo_root

            path = repo_root() / ".gitagent" / "state.db"
        _db = Database(Path(path))
    return _db


def reset_db() -> None:
    """Close and clear the singleton. Primarily for tests."""
    global _db
    if _db is not None:
        _db.close()
        _db = None