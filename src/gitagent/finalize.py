from __future__ import annotations

import shutil
from pathlib import Path

from . import gitwrap, review, store
from .errors import GitAgentError
from .models import ProposalState


def finalize(
    repo: Path | None = None,
    *,
    message: str,
    feature: str | None = None,
    target: str | None = None,
    sign: bool = False,
    keep_feature_branch: bool = False,
) -> str:
    """Integrate accepted proposals and produce a single commit on the target branch.

    Uses a detached temp worktree based on the target branch so the user's
    local checkout is never disturbed.  The target branch ref is updated via
    ``git update-ref`` after the commit.
    """
    repo = gitwrap.resolve(repo)
    store.require_init(repo)

    if feature is not None:
        p = store.paths_for_feature(repo, feature)
    else:
        p = store.current_feature_paths(repo)

    session = store.require_session(p)
    if session.state.value not in ("open", "integrating"):
        raise GitAgentError(f"Session is {session.state.value}; cannot finalize.")

    target_branch = target or session.target_branch

    # --- Phase 4: integrate against live target state --------------------
    review.integrate(repo, feature=feature)

    integrated = [
        pid
        for pid in store.proposal_ids(p)
        if store.load_review(p, pid).state == ProposalState.INTEGRATED
    ]
    if not integrated:
        raise GitAgentError(
            "No integrated proposals to finalize. "
            "Run `gitagent accept <pid>` and then `gitagent integrate` first."
        )

    # --- Phase 5: land via detached temp worktree -----------------------
    temp_wt = p.root / "_finalize" / session.feature_key

    try:
        gitwrap.worktree_add_detached(temp_wt, target_branch, cwd=repo)

        try:
            gitwrap.run(
                ["merge", "--squash", session.integration_branch],
                cwd=temp_wt,
            )
        except GitAgentError as exc:
            gitwrap.abort_merge(cwd=temp_wt)
            raise GitAgentError(
                f"Squash merge of integration into '{target_branch}' conflicted. "
                f"Resolve manually, or `gitagent abort` to discard.\n{exc}"
            ) from exc

        if gitwrap.is_clean(temp_wt):
            raise GitAgentError(
                "Nothing to commit after squash (integration produces no net changes)."
            )

        sha = gitwrap.commit(message, sign=sign, cwd=temp_wt)

        # Update the target branch ref to point to the new commit.
        gitwrap.run(["update-ref", f"refs/heads/{target_branch}", sha], cwd=repo)

    finally:
        # Always clean up the temp worktree, even if commit failed.
        if temp_wt.exists():
            gitwrap.worktree_remove(temp_wt, force=True, cwd=repo)
            gitwrap.worktree_prune(cwd=repo)

    # --- Sync working tree with the updated target ref -------------------
    # update-ref moved the branch pointer but the working tree may be stale.
    # Reset hard to the new commit so the user sees the landed files.
    cur_branch = gitwrap.current_branch(repo)
    if cur_branch == target_branch:
        gitwrap.run(["reset", "--hard", sha], cwd=repo)

    store.log_event(
        p,
        {
            "event": "finalize",
            "feature_key": p.feature.name,
            "session": session.id,
            "commit": sha,
            "message": message,
            "target_branch": target_branch,
        },
    )

    # --- Cleanup: always leave user on the target branch -----------------
    cur_branch = gitwrap.current_branch(repo)
    if cur_branch != target_branch:
        gitwrap.run(["checkout", "-q", target_branch], cwd=repo)

    if not keep_feature_branch:
        with __import__("contextlib").suppress(GitAgentError):
            gitwrap.branch_delete(session.branch, force=True, cwd=repo)

    # Remove .gitagent/features/<key>/ (keep global log).
    shutil.rmtree(p.feature, ignore_errors=True)

    return sha
