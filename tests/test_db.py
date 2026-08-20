"""Tests for db.py — v3 schema, migrations, indexes, thread safety."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from gitagent.db import CURRENT_VERSION, Database

V2_SQL = """
CREATE TABLE session (
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
CREATE TABLE agents (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES session(id),
    role        TEXT NOT NULL DEFAULT '',
    started_at  TEXT NOT NULL,
    ended_at    TEXT
);
CREATE TABLE intents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    intent      TEXT NOT NULL,
    ts          TEXT NOT NULL
);
CREATE TABLE edits (
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
CREATE TABLE inbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    to_agent   TEXT NOT NULL,
    from_agent TEXT,
    kind       TEXT NOT NULL,
    payload    TEXT,
    ts         TEXT NOT NULL,
    read       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_edits_file ON edits(session_id, file, ts);
CREATE INDEX IF NOT EXISTS idx_inbox_to ON inbox(to_agent, read, ts);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_session
    ON session(state) WHERE state = 'open';
"""


def _tables(db: Database):
    rows = db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    return {r["name"] for r in rows}


def _indexes(db: Database):
    rows = db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    )
    return {r["name"] for r in rows}


def _make_v2(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(V2_SQL)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()


def test_schema_creation(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    tables = _tables(db)
    assert "session" in tables
    assert "agents" in tables
    assert "intents" in tables
    assert "edits" in tables
    assert "snapshot_progress" in tables
    assert "locks" in tables
    assert "snapshots" in tables
    # Inbox is gone in v0.6.
    assert "inbox" not in tables
    db.close()


def test_user_version(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    version = db.conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == CURRENT_VERSION
    assert version == 3
    db.close()


def test_idempotent_migration(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.close()
    db2 = Database(tmp_path / "test.db")
    assert db2.conn.execute("PRAGMA user_version").fetchone()[0] == 3
    db2.close()


def test_replace_all_column(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    cols = {r["name"] for r in db.fetchall("PRAGMA table_info(edits)")}
    assert "replace_all" in cols
    db.close()


def test_lock_ttl_column(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    cols = {r["name"] for r in db.fetchall("PRAGMA table_info(session)")}
    assert "lock_ttl_seconds" in cols
    db.close()


def test_multi_open_sessions_allowed(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    now = "2026-01-01T00:00:00+00:00"
    db.executemany(
        """INSERT INTO session
           (id, feature, target_branch, base_sha, worktree, state, created_at)
           VALUES (?, ?, 'main', 'abc', ?, 'open', ?)""",
        [
            ("s_a", "feat-a", "wt_a", now),
            ("s_b", "feat-b", "wt_b", now),
        ],
    )
    db.commit()
    # The old idx_one_open_session invariant must not exist.
    assert "idx_one_open_session" not in _indexes(db)
    rows = db.fetchall("SELECT * FROM session WHERE state = 'open'")
    assert len(rows) == 2
    db.close()


def test_migration_v2_to_v3(tmp_path: Path):
    path = tmp_path / "v2.db"
    _make_v2(path)
    db = Database(path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 3

    tables = _tables(db)
    assert "inbox" not in tables
    assert "snapshot_progress" in tables
    assert "locks" in tables
    assert "snapshots" in tables
    assert "idx_one_open_session" not in _indexes(db)

    cols = {r["name"] for r in db.fetchall("PRAGMA table_info(edits)")}
    assert "replace_all" in cols
    cols2 = {r["name"] for r in db.fetchall("PRAGMA table_info(session)")}
    assert "lock_ttl_seconds" in cols2
    db.close()


def test_thread_safety(tmp_path: Path):
    """Verify multiple threads can use the same Database concurrently."""
    db = Database(tmp_path / "thread_test.db")
    errors: list[Exception] = []
    num_threads = 8
    ops_per_thread = 20

    def worker(thread_id: int):
        try:
            for i in range(ops_per_thread):
                db.execute(
                    "INSERT INTO intents (agent_id, kind, intent, ts) "
                    "VALUES (?, 'start', ?, ?)",
                    (f"agent-{thread_id}", f"intent-{i}", "2026-01-01T00:00:00"),
                )
                db.commit()
                rows = db.fetchall(
                    "SELECT COUNT(*) as cnt FROM intents WHERE agent_id = ?",
                    (f"agent-{thread_id}",),
                )
                assert rows[0]["cnt"] == i + 1
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread safety errors: {errors}"
    total = db.fetchone("SELECT COUNT(*) as cnt FROM intents")["cnt"]
    assert total == num_threads * ops_per_thread
    db.close()


def test_thread_local_connections(tmp_path: Path):
    db = Database(tmp_path / "local_test.db")
    conns: dict[int, int] = {}

    main_conn_id = id(db.conn)

    def worker(thread_id: int):
        conns[thread_id] = id(db.conn)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    all_ids = [main_conn_id] + list(conns.values())
    assert len(set(all_ids)) == 5
    db.close()