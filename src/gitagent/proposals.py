from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from . import gitwrap, store
from .errors import GitAgentError
from .models import AgentState, Proposal, ProposalState, Review


def _resolve(repo: Path | None, feature: str | None = None) -> tuple[Path, store.Paths]:
    repo = gitwrap.resolve(repo)
    if feature is not None:
        p = store.paths_for_feature(repo, feature)
    else:
        p = store.current_feature_paths(repo)
    return repo, p


def propose(
    repo: Path | None = None,
    *,
    agent_id: str,
    title: str,
    summary: str = "",
    confidence: float | None = None,
    feature: str | None = None,
) -> Proposal:
    repo, p = _resolve(repo, feature)
    session = store.require_session(p)
    if session.state.value not in ("open", "integrating"):
        raise GitAgentError(f"Session is {session.state.value}; cannot propose.")
    agent = store.load_agent(p, agent_id)
    if agent.state != AgentState.ACTIVE:
        raise GitAgentError(f"Agent '{agent_id}' is {agent.state.value}; cannot propose.")

    worktree = Path(agent.worktree)
    if not worktree.is_dir():
        raise GitAgentError(f"Agent worktree missing at {worktree}; was it removed?")

    gitwrap.run(["add", "-A"], cwd=worktree)
    files_out = gitwrap.run(
        ["diff", "--cached", "--name-only", agent.base_sha], cwd=worktree
    )
    files = [line for line in files_out.splitlines() if line.strip()]
    if not files:
        raise GitAgentError(f"Agent '{agent_id}' has no changes to propose.")

    patch = gitwrap.run(["diff", "--cached", "--binary", agent.base_sha], cwd=worktree)

    pid = "p_" + secrets.token_hex(4)
    proposal = Proposal(
        id=pid,
        agent_id=agent_id,
        base_sha=agent.base_sha,
        title=title,
        files=files,
        summary=summary,
        confidence=confidence,
        created_at=store.now(),
    )
    store.save_proposal(p, proposal)
    store.patch_path(p, pid).write_text(patch, encoding="utf-8")
    store.save_review(p, pid, Review(state=ProposalState.PENDING))
    store.log_event(
        p,
        {
            "event": "propose",
            "proposal": pid,
            "agent": agent_id,
            "title": title,
            "files": files,
        },
    )
    return proposal


def list_proposals(repo: Path | None = None, *, feature: str | None = None) -> list[dict[str, Any]]:
    repo, p = _resolve(repo, feature)
    store.require_session(p)
    out: list[dict[str, Any]] = []
    for pid in store.proposal_ids(p):
        try:
            proposal = store.load_proposal(p, pid)
            review = store.load_review(p, pid)
        except GitAgentError:
            continue
        out.append({"manifest": proposal.to_dict(), "review": review.to_dict()})
    return out


def get(
    repo: Path | None = None,
    *,
    proposal_id: str,
    feature: str | None = None,
) -> dict[str, Any]:
    repo, p = _resolve(repo, feature)
    store.require_session(p)
    proposal = store.load_proposal(p, proposal_id)
    review = store.load_review(p, proposal_id)
    return {"manifest": proposal.to_dict(), "review": review.to_dict()}


def read_patch(
    repo: Path | None = None,
    *,
    proposal_id: str,
    feature: str | None = None,
) -> str:
    repo, p = _resolve(repo, feature)
    store.require_session(p)
    return store.read_patch(p, proposal_id)
