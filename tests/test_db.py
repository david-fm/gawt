"""Tests for db.py — schema, migrations, indexes, thread safety."""
from __future__ import annotations

import threading
from pathlib import Path

from gitagent.db import CURRENT_VERSION, Database


def test_schema_creation(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    # Check tables exist
    rows = db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {r["name"] for r in rows}
    assert "session" in tables
    assert "agents" in tables
    assert "intents" in tables
    assert "edits" in tables
    assert "inbox" in tables
    db.close()


def test_user_version(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    version = db.conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == CURRENT_VERSION
    db.close()


def test_idempotent_migration(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.close()
    # Reopen — should not fail
    db2 = Database(tmp_path / "test.db")
    version = db2.conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == CURRENT_VERSION
    db2.close()


def test_indexes_exist(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    rows = db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    )
    names = {r["name"] for r in rows}
    assert "idx_edits_file" in names
    assert "idx_inbox_to" in names
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
                    "INSERT INTO inbox (to_agent, from_agent, kind, payload, ts) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"agent-{thread_id}", "test", "msg", f"payload-{i}", "2026-01-01T00:00:00"),
                )
                db.commit()
                rows = db.fetchall(
                    "SELECT COUNT(*) as cnt FROM inbox WHERE to_agent = ?",
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
    total = db.fetchone("SELECT COUNT(*) as cnt FROM inbox")["cnt"]
    assert total == num_threads * ops_per_thread
    db.close()


def test_thread_local_connections(tmp_path: Path):
    """Each thread gets its own SQLite connection object."""
    db = Database(tmp_path / "local_test.db")
    conns: dict[int, int] = {}

    # Initialize main thread connection first
    main_conn_id = id(db.conn)

    def worker(thread_id: int):
        conn_id = id(db.conn)
        conns[thread_id] = conn_id

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All thread connections should be different from each other and from main
    all_ids = [main_conn_id] + list(conns.values())
    assert len(set(all_ids)) == 5, f"Expected 5 unique connections, got {all_ids}"
    db.close()
