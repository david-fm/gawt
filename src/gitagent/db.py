"""SQLite state store for gitagent v0.5.0.

Provides a single-file database with PRAGMA user_version-based migrations.
All gitagent state (sessions, agents, intents, edits, inbox) lives here.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

CURRENT_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id          TEXT PRIMARY KEY,
    feature     TEXT NOT NULL,
    target_branch TEXT NOT NULL,
    base_sha    TEXT NOT NULL,
    worktree    TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT NOT NULL,
    ended_at    TEXT,
    final_sha   TEXT
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
    ts           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    to_agent   TEXT NOT NULL,
    from_agent TEXT,
    kind       TEXT NOT NULL,
    payload    TEXT,
    ts         TEXT NOT NULL,
    read       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_edits_file ON edits(session_id, file, ts);
CREATE INDEX IF NOT EXISTS idx_inbox_to   ON inbox(to_agent, read, ts);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply schema migrations using PRAGMA user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= CURRENT_VERSION:
        return
    if version == 0:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
        conn.commit()


class Database:
    """Thin wrapper around a sqlite3 connection with migration support."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            _migrate(self._conn)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

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
