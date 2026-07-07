from __future__ import annotations

from pathlib import Path
from typing import Any

from . import gitwrap, store
from .errors import GitAgentError
from .models import Proposal, ProposalState, Review, SessionState


def _apply(
    repo: Path,
    session,
    proposal: Proposal,
    review: Review,
) -> None:
    """Apply a proposal's patch onto the integration worktree and commit there.

    On success: state -> INTEGRATED, integrated=True.
    On conflict: state -> CONFLICT, raises.
    """
    int_wt = Path(session.integration_worktree)
    patch = store.patch_path(repo, proposal.id)

    try:
        gitwrap.run(["apply", "--3way", str(patch)], cwd=int_wt)
    except GitAgentError as exc:
        review.state = ProposalState.CONFLICT
        review.feedback = f"conflict applying patch: {exc}".strip()
        store.save_review(repo, proposal.id, review)
        store.log_event(repo, {"event": "conflict", "proposal": proposal.id})
        raise GitAgentError(
            f"Proposal {proposal.id} conflicts with current integration. "
            f"Marked 'conflict'. Resolve in {int_wt} or `gitagent revise {proposal.id}`."
        ) from exc

    unmerged = gitwrap.unmerged_files(cwd=int_wt)
    if unmerged:
        review.state = ProposalState.CONFLICT
        review.feedback = "conflict markers left in: " + ", ".join(unmerged)
        store.save_review(repo, proposal.id, review)
        store.log_event(
            repo, {"event": "conflict", "proposal": proposal.id, "files": unmerged}
        )
        raise GitAgentError(
            f"Proposal {proposal.id} produced conflicts in: {', '.join(unmerged)}. "
            f"Marked 'conflict'. Resolve in {int_wt} or `gitagent revise {proposal.id}`."
        )

    gitwrap.run(["add", "-A"], cwd=int_wt)
    sha = gitwrap.commit(f"gitagent: apply {proposal.id}", cwd=int_wt)
    review.state = ProposalState.INTEGRATED
    review.integrated = True
    review.integration_sha = sha
    review.applied_at = store.now()
    store.save_review(repo, proposal.id, review)


def _can_accept(review: Review) -> tuple[bool, str]:
    """Return (ok, reason).

    accept works from pending, conflict (retry), or accepted (idempotent).
    """
    if review.state == ProposalState.PENDING:
        return True, ""
    if review.state == ProposalState.CONFLICT:
        return True, ""
    if review.state == ProposalState.ACCEPTED and not review.integrated:
        return True, ""  # idempotent re-accept
    if review.state == ProposalState.INTEGRATED:
        return False, "already integrated"
    if review.state == ProposalState.REJECTED:
        return False, "already rejected"
    if review.state == ProposalState.REVISE:
        return False, "sent back for revision (wait for the agent to re-propose)"
    return False, f"state is '{review.state.value}'"


def accept(repo: Path | None = None, *, proposal_id: str) -> Review:
    """Mark a proposal as accepted (decision only).

    Does NOT apply the patch. Run `gitagent integrate` to apply all accepted proposals.
    Allowed transitions:
        pending   -> accepted
        conflict  -> accepted   (retry, after you cleaned the integration worktree)
        accepted  -> accepted   (idempotent, refreshes decided_at)
    """
    repo = gitwrap.resolve(repo)
    session = store.require_session(repo)
    if session.state.value not in ("open", "integrating"):
        raise GitAgentError(f"Session is {session.state.value}; cannot accept.")
    store.load_proposal(repo, proposal_id)  # validates existence

    with store.lock(repo, "integration"):
        review = store.load_review(repo, proposal_id)
        ok, reason = _can_accept(review)
        if not ok:
            raise GitAgentError(f"Proposal {proposal_id} cannot be accepted: {reason}.")
        was_integrated = review.integrated
        review.state = ProposalState.ACCEPTED
        review.decided_at = store.now()
        if not was_integrated:
            # resetting for a fresh accept (e.g. retry from conflict)
            review.applied_at = ""
            review.integration_sha = None
            review.integrated = False
        store.save_review(repo, proposal_id, review)

    store.log_event(repo, {"event": "accept", "proposal": proposal_id})
    return review


def _can_reject(review: Review) -> tuple[bool, str]:
    if review.state == ProposalState.INTEGRATED:
        return False, "already integrated"
    if review.state == ProposalState.REJECTED:
        return True, ""  # idempotent
    return True, ""


def reject(repo: Path | None = None, *, proposal_id: str, reason: str = "") -> Review:
    """Reject a proposal. Cannot reject an already-integrated proposal."""
    repo = gitwrap.resolve(repo)
    store.require_session(repo)
    store.load_proposal(repo, proposal_id)  # validates existence

    with store.lock(repo, "integration"):
        review = store.load_review(repo, proposal_id)
        ok, reason_msg = _can_reject(review)
        if not ok:
            raise GitAgentError(f"Proposal {proposal_id} cannot be rejected: {reason_msg}.")
        review.state = ProposalState.REJECTED
        review.reason = reason
        review.decided_at = store.now()
        store.save_review(repo, proposal_id, review)

    store.log_event(
        repo, {"event": "reject", "proposal": proposal_id, "reason": reason}
    )
    return review


def _can_revise(review: Review) -> tuple[bool, str]:
    if review.state == ProposalState.INTEGRATED:
        return False, "already integrated (the change is on the integration branch)"
    if review.state == ProposalState.REJECTED:
        return False, "already rejected"
    if review.state == ProposalState.REVISE:
        return True, ""  # idempotent, updates feedback
    return True, ""


def revise(repo: Path | None = None, *, proposal_id: str, feedback: str = "") -> Review:
    """Send a proposal back to the agent for another iteration.

    The agent should re-propose (a new pid is generated). The old pid stays as 'revise'.
    """
    repo = gitwrap.resolve(repo)
    store.require_session(repo)
    store.load_proposal(repo, proposal_id)  # validates existence

    with store.lock(repo, "integration"):
        review = store.load_review(repo, proposal_id)
        ok, reason_msg = _can_revise(review)
        if not ok:
            raise GitAgentError(f"Proposal {proposal_id} cannot be revised: {reason_msg}.")
        review.state = ProposalState.REVISE
        review.feedback = feedback
        review.decided_at = store.now()
        store.save_review(repo, proposal_id, review)

    store.log_event(
        repo,
        {"event": "revise", "proposal": proposal_id, "feedback": feedback},
    )
    return review


def integrate(repo: Path | None = None) -> dict[str, Any]:
    """Apply all accepted (or conflicted-retry) proposals onto the integration branch.

    Integration order is the proposal creation order (pid sorted). Proposals in states
    pending / rejected / revise are skipped. Already-integrated ones are reported
    as 'skipped'. On conflict, the proposal is marked 'conflict' and processing
    continues with the next proposal.

    Returns: {"applied": [...], "conflicted": [...], "skipped": [...]}.
    """
    repo = gitwrap.resolve(repo)
    session = store.require_session(repo)
    if session.state.value not in ("open", "integrating"):
        raise GitAgentError(f"Session is {session.state.value}; cannot integrate.")

    session.state = SessionState.INTEGRATING
    store.save_session(repo, session)

    applied: list[str] = []
    conflicted: list[str] = []
    skipped: list[str] = []

    items: list[tuple[str, str, Proposal, Review]] = []
    for pid in store.proposal_ids(repo):
        proposal = store.load_proposal(repo, pid)
        review = store.load_review(repo, pid)
        items.append((proposal.created_at, pid, proposal, review))
    items.sort(key=lambda x: (x[0], x[1]))

    with store.lock(repo, "integration"):
        for _, pid, proposal, review in items:
            if review.state in (
                ProposalState.PENDING,
                ProposalState.REJECTED,
                ProposalState.REVISE,
            ):
                continue
            if review.state == ProposalState.INTEGRATED:
                skipped.append(pid)
                continue
            if review.state in (ProposalState.ACCEPTED, ProposalState.CONFLICT):
                try:
                    _apply(repo, session, proposal, review)
                    applied.append(pid)
                except GitAgentError:
                    conflicted.append(pid)

    store.log_event(
        repo,
        {
            "event": "integrate",
            "applied": applied,
            "conflicted": conflicted,
            "skipped": skipped,
        },
    )
    return {"applied": applied, "conflicted": conflicted, "skipped": skipped}
