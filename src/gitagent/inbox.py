"""Inbox: check_inbox, send_message for inter-agent coordination."""
from __future__ import annotations

from datetime import UTC, datetime

from .agents import validate_agent
from .db import Database, get_db


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _send_inbox(
    to_agent: str,
    from_agent: str | None,
    kind: str,
    payload: dict,
    *,
    db: Database | None = None,
) -> None:
    """Insert an inbox row. Internal helper (used by edits.py for conflicts)."""
    db = db or get_db()
    import json
    now = _now()
    db.execute(
        """INSERT INTO inbox (to_agent, from_agent, kind, payload, ts)
           VALUES (?, ?, ?, ?, ?)""",
        (to_agent, from_agent, kind, json.dumps(payload), now),
    )
    db.commit()


def check_inbox(agent_id: str, *, db: Database | None = None) -> list[dict]:
    """Return unread inbox items for *agent_id* and mark them read."""
    db = db or get_db()
    validate_agent(agent_id, db)

    rows = db.fetchall(
        """SELECT * FROM inbox
           WHERE to_agent = ? AND read = 0
           ORDER BY ts""",
        (agent_id,),
    )

    if not rows:
        return []

    # Mark all read
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    db.execute(
        f"UPDATE inbox SET read = 1 WHERE id IN ({placeholders})",
        tuple(ids),
    )
    db.commit()

    return [dict(r) for r in rows]


def send_message(
    from_agent_id: str,
    to_agent_id: str,
    message: str,
    *,
    db: Database | None = None,
) -> dict:
    """Send a manual inbox message between agents.

    Returns ``{ok: True}``.
    """
    db = db or get_db()
    validate_agent(from_agent_id, db)
    validate_agent(to_agent_id, db)

    _send_inbox(
        to_agent_id, from_agent_id, "manual", {"message": message}, db=db
    )

    return {"ok": True}
