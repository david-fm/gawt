from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import gitwrap
from .errors import GitAgentError
from .models import Agent, Proposal, Review, Session

GITAGENT_DIR = ".gitagent"


def now() -> str:
    # datetime.strftime supports %f portably (time.strftime does not on macOS/BSD).
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class Paths:
    root: Path
    session: Path
    agents: Path
    proposals: Path
    integration: Path
    locks: Path
    log: Path


def paths(repo: Path) -> Paths:
    root = repo / GITAGENT_DIR
    return Paths(
        root=root,
        session=root / "session.json",
        agents=root / "agents",
        proposals=root / "proposals",
        integration=root / "integration",
        locks=root / "locks",
        log=root / "log.jsonl",
    )


def initialized(repo: Path) -> bool:
    return (repo / GITAGENT_DIR).is_dir()


def require_init(repo: Path) -> None:
    if not initialized(repo):
        raise GitAgentError("gitagent is not initialized. Run `gitagent init` first.")


def ensure_dirs(repo: Path) -> None:
    p = paths(repo)
    for d in (p.root, p.agents, p.proposals, p.integration, p.locks):
        d.mkdir(parents=True, exist_ok=True)


def load_session(repo: Path) -> Session | None:
    p = paths(repo).session
    if not p.exists():
        return None
    return Session.from_dict(_read_json(p))


def require_session(repo: Path) -> Session:
    s = load_session(repo)
    if s is None:
        raise GitAgentError("No active session. Run `gitagent start --feature <name>` first.")
    return s


def save_session(repo: Path, session: Session) -> None:
    session.updated_at = now()
    _write_json(paths(repo).session, session.to_dict())


def agent_ids(repo: Path) -> list[str]:
    d = paths(repo).agents
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def agent_dir(repo: Path, agent_id: str) -> Path:
    return paths(repo).agents / agent_id


def load_agent(repo: Path, agent_id: str) -> Agent:
    f = agent_dir(repo, agent_id) / "meta.json"
    if not f.exists():
        raise GitAgentError(f"No agent with id '{agent_id}'.")
    return Agent.from_dict(_read_json(f))


def save_agent(repo: Path, agent: Agent) -> None:
    agent_dir(repo, agent.id).mkdir(parents=True, exist_ok=True)
    _write_json(agent_dir(repo, agent.id) / "meta.json", agent.to_dict())


def proposal_ids(repo: Path) -> list[str]:
    d = paths(repo).proposals
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def proposal_dir(repo: Path, proposal_id: str) -> Path:
    return paths(repo).proposals / proposal_id


def load_proposal(repo: Path, proposal_id: str) -> Proposal:
    f = proposal_dir(repo, proposal_id) / "manifest.json"
    if not f.exists():
        raise GitAgentError(f"No proposal with id '{proposal_id}'.")
    return Proposal.from_dict(_read_json(f))


def save_proposal(repo: Path, proposal: Proposal) -> None:
    d = proposal_dir(repo, proposal.id)
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / "manifest.json", proposal.to_dict())


def patch_path(repo: Path, proposal_id: str) -> Path:
    return proposal_dir(repo, proposal_id) / "change.patch"


def read_patch(repo: Path, proposal_id: str) -> str:
    p = patch_path(repo, proposal_id)
    if not p.exists():
        raise GitAgentError(f"No patch for proposal '{proposal_id}'.")
    return p.read_text()


def load_review(repo: Path, proposal_id: str) -> Review:
    f = proposal_dir(repo, proposal_id) / "review.json"
    if not f.exists():
        return Review()
    return Review.from_dict(_read_json(f))


def save_review(repo: Path, proposal_id: str, review: Review) -> None:
    proposal_dir(repo, proposal_id).mkdir(parents=True, exist_ok=True)
    _write_json(proposal_dir(repo, proposal_id) / "review.json", review.to_dict())


def log_event(repo: Path, event: dict[str, Any]) -> None:
    p = paths(repo).log
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": now(), **event}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def read_log(repo: Path) -> list[dict[str, Any]]:
    p = paths(repo).log
    if not p.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


@contextlib.contextmanager
def lock(repo: Path, name: str) -> Iterator[None]:
    d = paths(repo).locks
    d.mkdir(parents=True, exist_ok=True)
    lf = d / f"{name}.lock"
    fd = os.open(lf, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def teardown(repo: Path, session: Session, *, keep_log: bool = True) -> None:
    """Remove all worktrees/branches for the session and reset .gitagent state."""
    for agent_id in agent_ids(repo):
        try:
            agent = load_agent(repo, agent_id)
        except GitAgentError:
            continue
        gitwrap.worktree_remove(agent.worktree, force=True, cwd=repo)
        gitwrap.branch_delete(agent.branch, cwd=repo)
        gitwrap.worktree_prune(cwd=repo)

    gitwrap.worktree_remove(session.integration_worktree, force=True, cwd=repo)
    gitwrap.branch_delete(session.integration_branch, cwd=repo)
    gitwrap.worktree_prune(cwd=repo)

    p = paths(repo)
    for target in (p.agents, p.proposals, p.integration, p.locks):
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
    if p.session.exists():
        p.session.unlink()
    if not keep_log and p.log.exists():
        p.log.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
