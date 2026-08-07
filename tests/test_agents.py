"""Tests for agents.py — register, unregister, list, validate."""
from __future__ import annotations

import pytest

from gitagent import agents, session
from gitagent.errors import GitAgentError


def test_register_agent(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("agent-test", db=db)

    result = agents.register_agent("implementer", db=db)
    assert result["agent_id"].startswith("a_")


def test_list_agents(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("list-test", db=db)

    agents.register_agent("one", db=db)
    agents.register_agent("two", db=db)

    lst = agents.list_agents(db=db)
    assert len(lst) == 2
    assert lst[0]["role"] == "one"
    assert lst[1]["role"] == "two"


def test_unregister_agent(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("unreg", db=db)

    result = agents.register_agent("temp", db=db)
    aid = result["agent_id"]
    agents.unregister_agent(aid, db=db)

    lst = agents.list_agents(db=db)
    assert lst[0]["ended_at"] is not None


def test_validate_agent_active(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("validate", db=db)

    result = agents.register_agent("active", db=db)
    aid = result["agent_id"]
    a = agents.validate_agent(aid, db=db)
    assert a["id"] == aid
    assert a["ended_at"] is None


def test_validate_agent_ended(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("ended", db=db)

    result = agents.register_agent("done", db=db)
    aid = result["agent_id"]
    agents.unregister_agent(aid, db=db)

    with pytest.raises(GitAgentError, match="ended"):
        agents.validate_agent(aid, db=db)


def test_validate_agent_not_found(repo_with_gitagent):
    repo, db = repo_with_gitagent
    session.start_session("nope", db=db)

    with pytest.raises(GitAgentError, match="not found"):
        agents.validate_agent("a_deadbeef", db=db)


def test_register_without_session(repo_with_gitagent):
    repo, db = repo_with_gitagent
    with pytest.raises(GitAgentError, match="No open session"):
        agents.register_agent("orphan", db=db)
