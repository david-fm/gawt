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

from . import feature, gitwrap
from .errors import GitAgentError
from .models import Agent, Proposal, Review, Session

GITAGENT_DIR = ".gitagent"
FEATURES_DIR = "features"


def now() -> str:
    # datetime.strftime supports %f portably (time.strftime does not on macOS/BSD).
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class Paths:
    root: Path
    feature: Path
    session: Path
    agents: Path
    proposals: Path
    integration: Path
    locks: Path
    log: Path


def paths(repo: Path, feature_key: str) -> Paths:
    root = repo / GITAGENT_DIR
    f = root / FEATURES_DIR / feature_key
    return Paths(
        root=root,
        feature=f,
        session=f / "session.json",
        agents=f / "agents",
        proposals=f / "proposals",
        integration=f / "integration",
        locks=f / "locks",
        log=root / "log.jsonl",
    )


def global_log_path(repo: Path) -> Path:
    """The path of the global, cross-feature audit log."""
    return repo / GITAGENT_DIR / "log.jsonl"


def log_event_at(log_path: Path, event: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": now(), **event}
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def read_log_at(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def initialized(repo: Path) -> bool:
    return (repo / GITAGENT_DIR).is_dir()


def require_init(repo: Path) -> None:
    if not initialized(repo):
        raise GitAgentError("gitagent is not initialized. Run `gitagent init` first.")


def list_features(repo: Path) -> list[str]:
    """Return sorted feature keys present in the .gitagent/features/ directory."""
    d = repo / GITAGENT_DIR / FEATURES_DIR
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def paths_for_feature(repo: Path, feature_name: str) -> Paths:
    """Resolve paths for a feature by name, regardless of the current branch.

    Accepts 'daily-env-netcdf' or 'ga/daily-env-netcdf' — both resolve to the
    same directory under .gitagent/features/<key>/.
    """
    require_init(repo)
    key = feature.coerce(feature_name)
    return paths(repo, key)


def current_feature_paths(repo: Path) -> Paths:
    """Resolve paths for the active feature based on the current git branch.

    The branch must be a feature branch (prefix ``ga/``).  Returns
    ``paths_for_feature`` once the branch is resolved.

    .. deprecated::
        Prefer ``paths_for_feature`` with an explicit feature name.  The
        current-branch fallback is kept for backward compatibility and will
        emit a warning when the branch is not ``ga/<feature>``.
    """
    require_init(repo)
    branch = gitwrap.current_branch(repo)
    if branch is None:
        raise GitAgentError("HEAD is detached. Check out a feature branch to operate.")
    if not feature.is_feature_branch(branch):
        raise GitAgentError(
            f"Current branch '{branch}' is not a feature branch. "
            f"Run `git checkout -b {feature.FEATURE_PREFIX}<name>` to create one, "
            f"or pass --feature <name> to specify the feature explicitly."
        )
    return paths_for_feature(repo, branch)


def ensure_dirs(p: Paths) -> None:
    for d in (p.root, p.feature, p.agents, p.proposals, p.integration, p.locks):
        d.mkdir(parents=True, exist_ok=True)


def load_session(p: Paths) -> Session | None:
    if not p.session.exists():
        return None
    return Session.from_dict(_read_json(p.session))


def require_session(p: Paths) -> Session:
    s = load_session(p)
    if s is None:
        raise GitAgentError("No active session for this feature. Run `gitagent start` first.")
    return s


def require_session_for(repo: Path, feature_name: str) -> Session:
    """Load a session by feature name (not branch-dependent)."""
    p = paths_for_feature(repo, feature_name)
    return require_session(p)


def save_session(p: Paths, session: Session) -> None:
    session.updated_at = now()
    _write_json(p.session, session.to_dict())


def agent_ids(p: Paths) -> list[str]:
    d = p.agents
    if not d.exists():
        return []
    return sorted(x.name for x in d.iterdir() if x.is_dir())


def agent_dir(p: Paths, agent_id: str) -> Path:
    return p.agents / agent_id


def load_agent(p: Paths, agent_id: str) -> Agent:
    f = agent_dir(p, agent_id) / "meta.json"
    if not f.exists():
        raise GitAgentError(f"No agent with id '{agent_id}' in this feature.")
    return Agent.from_dict(_read_json(f))


def save_agent(p: Paths, agent: Agent) -> None:
    agent_dir(p, agent.id).mkdir(parents=True, exist_ok=True)
    _write_json(agent_dir(p, agent.id) / "meta.json", agent.to_dict())


def proposal_ids(p: Paths) -> list[str]:
    d = p.proposals
    if not d.exists():
        return []
    return sorted(x.name for x in d.iterdir() if x.is_dir())


def proposal_dir(p: Paths, proposal_id: str) -> Path:
    return p.proposals / proposal_id


def load_proposal(p: Paths, proposal_id: str) -> Proposal:
    f = proposal_dir(p, proposal_id) / "manifest.json"
    if not f.exists():
        raise GitAgentError(f"No proposal with id '{proposal_id}' in this feature.")
    return Proposal.from_dict(_read_json(f))


def save_proposal(p: Paths, proposal: Proposal) -> None:
    d = proposal_dir(p, proposal.id)
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / "manifest.json", proposal.to_dict())


def patch_path(p: Paths, proposal_id: str) -> Path:
    return proposal_dir(p, proposal_id) / "change.patch"


def read_patch(p: Paths, proposal_id: str) -> str:
    fp = patch_path(p, proposal_id)
    if not fp.exists():
        raise GitAgentError(f"No patch for proposal '{proposal_id}'.")
    return fp.read_text()


def load_review(p: Paths, proposal_id: str) -> Review:
    f = proposal_dir(p, proposal_id) / "review.json"
    if not f.exists():
        return Review()
    return Review.from_dict(_read_json(f))


def save_review(p: Paths, proposal_id: str, review: Review) -> None:
    proposal_dir(p, proposal_id).mkdir(parents=True, exist_ok=True)
    _write_json(proposal_dir(p, proposal_id) / "review.json", review.to_dict())


def log_event(p: Paths, event: dict[str, Any]) -> None:
    p.log.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": now(), **event}
    with p.log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def read_log(p: Paths) -> list[dict[str, Any]]:
    if not p.log.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in p.log.read_text().splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


@contextlib.contextmanager
def lock(p: Paths, name: str) -> Iterator[None]:
    d = p.locks
    d.mkdir(parents=True, exist_ok=True)
    lf = d / f"{name}.lock"
    fd = os.open(lf, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def teardown(
    p: Paths,
    session: Session,
    *,
    keep_log: bool = True,
    delete_integration_branch: bool = True,
) -> None:
    """Reset a single feature's state.

    Removes all agent worktrees/ephemeral branches and the integration worktree.
    By default, also deletes the integration branch (it has been consumed by
    `finalize` or is no longer needed). The feature branch itself is always
    preserved — that is the user's commit they want to merge.
    """
    repo = p.root.parent
    for agent_id in agent_ids(p):
        try:
            agent = load_agent(p, agent_id)
        except GitAgentError:
            continue
        with contextlib.suppress(GitAgentError):
            gitwrap.worktree_remove(agent.worktree, force=True, cwd=repo)
            gitwrap.branch_delete(agent.branch, cwd=repo)
        gitwrap.worktree_prune(cwd=repo)

    with contextlib.suppress(GitAgentError):
        gitwrap.worktree_remove(session.integration_worktree, force=True, cwd=repo)
    if delete_integration_branch:
        with contextlib.suppress(GitAgentError):
            gitwrap.branch_delete(session.integration_branch, cwd=repo)
    gitwrap.worktree_prune(cwd=repo)

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
