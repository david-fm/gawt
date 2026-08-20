"""Snapshot: partial commits, status, and crash reconciliation.

Multi-orchestrator flow: several sessions share ONE live worktree. An
orchestrator decides which part of the worktree is theirs and calls
``snapshot_session`` — a partial commit onto the worktree's target branch that
does NOT delete the live worktree. ``snapshot_status`` shows the whole worktree
vs the target, with the pheromone (edit history + intent) per file.

Crash reconciliation: disk is the source of truth. If the worktree differs
from the target but no ``edits`` row explains a file, we insert a synthetic
``adjusted`` attribution row so replay never fails with REPLAY_MISMATCH.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from . import gitwrap, replay, session
from .db import Database, get_db
from .edits import WARNING, _edit_rows
from .errors import GitAgentError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _open_session_row(session_id: str, db: Database) -> dict:
    srow = session.get_session(session_id, db=db)
    if srow is None or srow["state"] != "open":
        raise GitAgentError(f"No open session '{session_id}'.")
    return srow


def reconcile_untracked(db: Database) -> None:
    """Insert synthetic 'adjusted' edit rows for disk state without log entries.

    Run at the start of snapshot_status / snapshot_session. Every file that
    differs from the target but has zero edit rows gets an ``adjusted`` row
    attributed to '<unknown>' so the pheromone stays gapless.
    """
    sessions = db.fetchall(
        "SELECT * FROM session WHERE state = 'open' ORDER BY created_at"
    )
    if not sessions:
        return {"inserted": 0}
    srow = dict(sessions[0])
    wt = Path(srow["worktree"])
    target = srow["target_branch"]
    changed = gitwrap.list_files_vs_ref(target, cwd=wt)

    inserted = 0
    for f in changed:
        existing = db.fetchone(
            "SELECT 1 FROM edits WHERE file = ? LIMIT 1", (f,)
        )
        if existing is not None:
            continue
        db.execute(
            """INSERT INTO edits
               (agent_id, session_id, file, op, intent_id, replace_all, ts)
               VALUES ('<unknown>', ?, ?, 'adjusted', NULL, 0, ?)""",
            (srow["id"], f, _now()),
        )
        inserted += 1
    if inserted:
        db.commit()
    return {"inserted": inserted}


def snapshot_status(session_id: str, *, db: Database | None = None) -> dict:
    """Return the state of the snapshot as a whole-worktree git status/diff."""
    db = db or get_db()
    srow = _open_session_row(session_id, db)
    reconcile_untracked(db)

    wt = Path(srow["worktree"])
    repo = gitwrap.main_repo_root(wt)
    target = srow["target_branch"]
    base_sha = gitwrap.current_sha(cwd=repo)
    changed = gitwrap.list_files_vs_ref(target, cwd=wt)

    files = []
    for f in sorted(changed):
        status = changed[f]
        _, diff = gitwrap.diff_vs_ref(target, f, cwd=wt)
        edits = _edit_rows(f, db)
        progress = db.fetchall(
            """SELECT session_id, last_edit_id, last_ts
               FROM snapshot_progress WHERE file = ?""",
            (f,),
        )
        files.append(
            {
                "file": f,
                "status": status,
                "diff": diff,
                "edits": edits,
                "snapshot_progress": [
                    {
                        "session_id": p["session_id"],
                        "last_edit_id": p["last_edit_id"],
                        "last_ts": p["last_ts"],
                    }
                    for p in progress
                ],
            }
        )

    return {
        "worktree": str(wt),
        "target_branch": target,
        "base_sha": base_sha,
        "files": files,
        "pending_files_count": len(files),
        "warning": WARNING,
    }


def snapshot_session(
    session_id: str,
    message: str,
    *,
    boundary_edit_id: int | None = None,
    files: list[str] | None = None,
    sign: bool = False,
    db: Database | None = None,
) -> dict:
    """Commit a partial snapshot of the shared worktree onto the target branch.

    - ``files=None`` -> every file that ever had an edit row.
    - ``files=[...]`` -> exactly those files (partial commit).
    - ``boundary_edit_id`` -> commit the state reconstructed at that boundary
      via replay; otherwise commit current disk content (fast path).

    The live worktree is NOT deleted. Per-file snapshot progress advances only
    for files in scope. Non-scope files stay pending for the next iteration.
    """
    db = db or get_db()
    srow = _open_session_row(session_id, db)
    reconcile_untracked(db)

    repo = gitwrap.main_repo_root(Path(srow["worktree"]))
    wt = Path(srow["worktree"])
    target = srow["target_branch"]

    if files is None:
        scope_rows = db.fetchall("SELECT DISTINCT file FROM edits")
        scope = {r["file"] for r in scope_rows}
    else:
        scope = set(files)
        if not scope:
            raise GitAgentError("Nothing to snapshot: empty files list.")

    if not scope:
        raise GitAgentError("Nothing to snapshot: no edits recorded.")

    # Resolve per-file content: replay at boundary, or current disk state.
    plan: dict[str, str | None] = {}
    for f in scope:
        if boundary_edit_id is not None:
            content = replay.reconstruct(
                f, boundary_edit_id, db=db, target_ref=target, repo=repo
            )
        else:
            tpath = wt / f
            content = (
                tpath.read_text(encoding="utf-8") if tpath.exists() else None
            )
        plan[f] = content

    temp_wt = repo / ".gitagent" / "_snapshot_temp"
    if temp_wt.exists():
        gitwrap.worktree_remove(temp_wt, force=True, cwd=repo)
        gitwrap.worktree_prune(cwd=repo)
    try:
        gitwrap.worktree_add_detached(temp_wt, target, cwd=repo)
        for f, content in plan.items():
            tf = temp_wt / f
            if content is None:
                if tf.exists():
                    tf.unlink()
                continue
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text(content, encoding="utf-8")

        gitwrap.run(["add", "--", *sorted(scope)], cwd=temp_wt)
        staged = gitwrap.run(
            ["diff", "--cached", "--name-only"], cwd=temp_wt
        ).strip()
        if not staged:
            raise GitAgentError(
                "Nothing to snapshot (no net changes vs target)."
            )

        sha = gitwrap.commit(message, sign=sign, cwd=temp_wt)
        gitwrap.update_ref(f"refs/heads/{target}", sha, cwd=repo)
    finally:
        if temp_wt.exists():
            gitwrap.worktree_remove(temp_wt, force=True, cwd=repo)
            gitwrap.worktree_prune(cwd=repo)

    # Advance per-file snapshot progress — ONLY for files in scope.
    for f in scope:
        if boundary_edit_id is not None:
            last = db.fetchone(
                "SELECT MAX(id) AS maxid FROM edits WHERE file = ? AND id <= ?",
                (f, boundary_edit_id),
            )
        else:
            last = db.fetchone(
                "SELECT MAX(id) AS maxid FROM edits WHERE file = ?", (f,)
            )
        last_id = last["maxid"] if last and last["maxid"] is not None else 0
        last_ts = None
        if last_id:
            tsrow = db.fetchone("SELECT ts FROM edits WHERE id = ?", (last_id,))
            last_ts = tsrow["ts"] if tsrow else None
        db.execute(
            """INSERT INTO snapshot_progress (session_id, file, last_edit_id, last_ts)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id, file) DO UPDATE SET
                 last_edit_id = excluded.last_edit_id,
                 last_ts = excluded.last_ts""",
            (session_id, f, last_id, last_ts),
        )
    db.commit()

    scope_sorted = sorted(scope)
    db.execute(
        """INSERT INTO snapshots
           (session_id, message, boundary_edit_id, files, sha, ts)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, message, boundary_edit_id, json.dumps(scope_sorted), sha, _now()),
    )
    db.commit()

    return {
        "status": "snapshotted",
        "sha": sha,
        "files": scope_sorted,
        "boundary_edit_id": boundary_edit_id,
    }


def list_snapshots(
    session_id: str | None = None, *, db: Database | None = None
) -> list[dict]:
    """Return recorded snapshots, optionally filtered by session, newest first."""
    db = db or get_db()
    if session_id is not None:
        rows = db.fetchall(
            "SELECT * FROM snapshots WHERE session_id = ? ORDER BY id DESC",
            (session_id,),
        )
    else:
        rows = db.fetchall("SELECT * FROM snapshots ORDER BY id DESC")
    return [dict(r) for r in rows]