"""Tests for inbox.py — check_inbox, send_message, unread/read transitions."""
from __future__ import annotations

from gitagent import agents, inbox, session


def _setup_two_agents(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("inbox-test", db=db)
    a1 = agents.register_agent("agent-1", db=db)["agent_id"]
    a2 = agents.register_agent("agent-2", db=db)["agent_id"]
    return repo, db, a1, a2


def test_send_and_check(repo_with_gitagent):
    repo, db, a1, a2 = _setup_two_agents(repo_with_gitagent)

    inbox.send_message(a1, a2, "hello from a1", db=db)

    items = inbox.check_inbox(a2, db=db)
    assert len(items) == 1
    assert items[0]["from_agent"] == a1
    assert items[0]["kind"] == "manual"
    assert "hello from a1" in items[0]["payload"]


def test_check_marks_read(repo_with_gitagent):
    repo, db, a1, a2 = _setup_two_agents(repo_with_gitagent)

    inbox.send_message(a1, a2, "msg", db=db)
    inbox.check_inbox(a2, db=db)

    # Second check returns empty
    items = inbox.check_inbox(a2, db=db)
    assert items == []


def test_inbox_empty(repo_with_gitagent):
    repo, db, a1, a2 = _setup_two_agents(repo_with_gitagent)

    items = inbox.check_inbox(a1, db=db)
    assert items == []


def test_multiple_messages_fifo(repo_with_gitagent):
    repo, db, a1, a2 = _setup_two_agents(repo_with_gitagent)

    inbox.send_message(a1, a2, "first", db=db)
    inbox.send_message(a2, a2, "second", db=db)

    items = inbox.check_inbox(a2, db=db)
    assert len(items) == 2
    # FIFO order
    assert "first" in items[0]["payload"]
    assert "second" in items[1]["payload"]
