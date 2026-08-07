"""Session lifecycle: start, finalize, abort, get.

A session owns a single global worktree. Only one session can be open at a
time. Features are serialized; parallelism lives in the agents, not in
worktrees.
"""
from __future__ import annotations

import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import gitwrap
from .db import Database, get_db
from .errors import GitAgentError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sid() -> str:
    return f"s_{secrets.token_hex(4)}"


def get_session(db: Database | None = None) -> dict | None:
    """Return the currently open session as a dict, or None."""
    db = db or get_db()
    row = db.fetchone("SELECT * FROM session WHERE state = 'open'")
    return dict(row) if row else None


def start_session(
    feature: str,
    target_branch: str = "main",
    db: Database | None = None,
    conflict_window_seconds: int = 30,
) -> dict:
    """Create a new session with a detached worktree.

    Fails if a session is already open.

    Returns ``{session_id, worktree, base_sha}``.
    """
    db = db or get_db()

    existing = get_session(db)
    if existing is not None:
        raise GitAgentError(
            f"Session already open (id={existing['id']}, feature={existing['feature']}). "
            "Finalize or abort it first."
        )

    repo = gitwrap.repo_root()
    base_sha = gitwrap.current_sha(cwd=repo)

    wt_path = repo / ".gitagent" / "worktree"
    if wt_path.exists():
        shutil.rmtree(wt_path, ignore_errors=True)

    gitwrap.worktree_add_detached(wt_path, base_sha, cwd=repo)

    sid = _sid()
    now = _now()

    db.execute(
        """INSERT INTO session
           (id, feature, target_branch, base_sha, worktree, state, created_at)
           VALUES (?, ?, ?, ?, ?, 'open', ?)""",
        (sid, feature, target_branch, base_sha, str(wt_path), now),
    )
    db.commit()

    return {"session_id": sid, "worktree": str(wt_path), "base_sha": base_sha}


def abort_session(db: Database | None = None) -> None:
    """Remove the worktree and mark the session aborted."""
    db = db or get_db()
    session = get_session(db)
    if session is None:
        raise GitAgentError("No open session to abort.")

    repo = gitwrap.repo_root()
    wt = Path(session["worktree"])

    gitwrap.worktree_remove(wt, force=True, cwd=repo)
    gitwrap.worktree_prune(cwd=repo)

    now = _now()
    db.execute(
        "UPDATE session SET state = 'aborted', ended_at = ? WHERE id = ?",
        (now, session["id"]),
    )
    db.commit()


def finalize_session(
    message: str,
    *,
    sign: bool = False,
    db: Database | None = None,
) -> str:
    """Commit worktree state onto the target branch.

    Flow:
      1. git add -A + commit on the worktree
      2. create detached temp worktree on target_branch
      3. squash-merge the worktree commit into temp
      4. commit on temp
      5. git update-ref to advance target_branch
      6. clean up both worktrees
      7. mark session finalized

    Returns the final commit SHA on target_branch.
    """
    db = db or get_db()
    session = get_session(db)
    if session is None:
        raise GitAgentError("No open session to finalize.")

    repo = gitwrap.repo_root()
    wt = Path(session["worktree"])
    target_branch = session["target_branch"]

    if not wt.exists():
        raise GitAgentError(f"Worktree missing: {wt}")

    # Warn (not block) if agents are still active
    active = db.fetchall(
        "SELECT id FROM agents WHERE session_id = ? AND ended_at IS NULL",
        (session["id"],),
    )
    # TODO: emit warning to stderr if active agents exist

    # Phase 1: commit on the worktree
    gitwrap.run(["add", "-A"], cwd=wt)
    if gitwrap.is_clean(cwd=wt):
        raise GitAgentError("Nothing to commit (worktree is clean).")

    worktree_sha = gitwrap.commit(message, sign=sign, cwd=wt)

    # Phase 2-5: land on target branch via detached temp worktree
    temp_wt = repo / ".gitagent" / "_finalize_temp"
    if temp_wt.exists():
        gitwrap.worktree_remove(temp_wt, force=True, cwd=repo)
        gitwrap.worktree_prune(cwd=repo)

    try:
        gitwrap.worktree_add_detached(temp_wt, target_branch, cwd=repo)

        try:
            gitwrap.run(["merge", "--squash", worktree_sha], cwd=temp_wt)
        except GitAgentError as exc:
            gitwrap.abort_merge(cwd=temp_wt)
            raise GitAgentError(
                f"Squash merge into '{target_branch}' conflicted. "
                f"Resolve manually or abort.\n{exc}"
            ) from exc

        if gitwrap.is_clean(cwd=temp_wt):
            raise GitAgentError(
                "Nothing to commit after squash (no net changes vs target)."
            )

        final_sha = gitwrap.commit(message, sign=sign, cwd=temp_wt)

        gitwrap.run(
            ["update-ref", f"refs/heads/{target_branch}", final_sha],
            cwd=repo,
        )

    finally:
        # Always clean up both worktrees
        if temp_wt.exists():
            gitwrap.worktree_remove(temp_wt, force=True, cwd=repo)
            gitwrap.worktree_prune(cwd=repo)

    # Also remove the main worktree
    if wt.exists():
        gitwrap.worktree_remove(wt, force=True, cwd=repo)
        gitwrap.worktree_prune(cwd=repo)

    # If the user is on the target branch, refresh their checkout
    cur_branch = gitwrap.current_branch(repo)
    if cur_branch == target_branch:
        gitwrap.reset_hard(final_sha, cwd=repo)

    now = _now()
    db.execute(
        """UPDATE session
           SET state = 'finalized', ended_at = ?, final_sha = ?
           WHERE id = ?""",
        (now, final_sha, session["id"]),
    )
    db.commit()

    _append_log(session, message, final_sha)

    return final_sha


def _append_log(session: dict, message: str, sha: str) -> None:
    """Append a summary to .gitagent/log.jsonl (best-effort)."""
    import json
    try:
        repo = gitwrap.repo_root()
        log_path = repo / ".gitagent" / "log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "event": "finalize",
            "session_id": session["id"],
            "feature": session["feature"],
            "commit": sha,
            "message": message,
            "target_branch": session["target_branch"],
            "ts": _now(),
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
