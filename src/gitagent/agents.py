from __future__ import annotations

from pathlib import Path
from typing import Any

from . import gitwrap, store
from .errors import GitAgentError
from .models import Agent, AgentState


def spawn(
    repo: Path | None = None,
    *,
    agent_id: str,
    base: str | None = None,
    role: str = "",
) -> Agent:
    repo = gitwrap.resolve(repo)
    session = store.require_session(repo)
    if session.state.value not in ("open", "integrating"):
        raise GitAgentError(f"Session is {session.state.value}; cannot spawn agents.")
    if not agent_id:
        raise GitAgentError("Agent id cannot be empty.")
    if agent_id in store.agent_ids(repo):
        raise GitAgentError(f"Agent '{agent_id}' already exists.")

    base_ref = base or session.base_sha
    if not gitwrap.run_ok(["rev-parse", "--verify", f"{base_ref}^{{commit}}"], cwd=repo):
        raise GitAgentError(f"Base ref '{base_ref}' does not resolve to a commit.")
    base_sha = gitwrap.run(["rev-parse", base_ref], cwd=repo).strip()

    branch = f"agent/{agent_id}/{session.id}"
    worktree = repo / store.GITAGENT_DIR / "agents" / agent_id / "worktree"

    try:
        gitwrap.worktree_add(worktree, branch, base_ref, cwd=repo)
    except GitAgentError as exc:
        raise GitAgentError(f"Failed to create worktree for '{agent_id}'.\n{exc}") from exc

    agent = Agent(
        id=agent_id,
        role=role,
        base_sha=base_sha,
        base_ref=base_ref,
        branch=branch,
        worktree=str(worktree),
        state=AgentState.ACTIVE,
        created_at=store.now(),
    )
    store.save_agent(repo, agent)
    store.log_event(
        repo,
        {"event": "spawn", "agent": agent_id, "base": base_ref, "branch": branch},
    )
    return agent


def kill(repo: Path | None = None, *, agent_id: str) -> None:
    repo = gitwrap.resolve(repo)
    store.require_session(repo)
    agent = store.load_agent(repo, agent_id)
    gitwrap.worktree_remove(agent.worktree, force=True, cwd=repo)
    gitwrap.branch_delete(agent.branch, cwd=repo)
    gitwrap.worktree_prune(cwd=repo)
    agent.state = AgentState.KILLED
    store.save_agent(repo, agent)
    store.log_event(repo, {"event": "kill", "agent": agent_id})


def list_agents(repo: Path | None = None) -> list[dict[str, Any]]:
    repo = gitwrap.resolve(repo)
    store.require_session(repo)
    out: list[dict[str, Any]] = []
    for aid in store.agent_ids(repo):
        try:
            out.append(store.load_agent(repo, aid).to_dict())
        except GitAgentError:
            continue
    return out
