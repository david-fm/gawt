from __future__ import annotations

import functools
import json as _json
import sys
from collections.abc import Callable
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from . import agents as agents_mod
from . import feature as feature_mod
from . import finalize as finalize_mod
from . import gitwrap, store
from . import proposals as proposals_mod
from . import review as review_mod
from . import session as session_mod
from . import skill as skill_mod
from .errors import GitAgentError

app = typer.Typer(
    name="gitagent",
    help="Agent workspace manager over Git: isolate, review and consolidate multi-agent changes.",
    no_args_is_help=True,
    add_completion=True,
    pretty_exceptions_enable=False,
)
console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_LEGACY_WARNED: set[str] = set()


def _catch(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except GitAgentError as exc:
            err_console.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

    return wrapper


def _print_json(obj: Any) -> None:
    sys.stdout.write(_json.dumps(obj, indent=2, sort_keys=True) + "\n")


def _resolve_paths(feature: str | None = None) -> store.Paths:
    """Resolve paths for a feature, using --feature or the current branch."""
    repo = gitwrap.resolve(None)
    if feature is not None:
        return store.paths_for_feature(repo, feature)
    return store.current_feature_paths(repo)


def _warn_legacy(command: str, feature: str | None) -> None:
    """Emit a deprecation warning when feature is inferred from the current branch."""
    if feature is not None:
        return
    key = f"warn-{command}"
    if key in _LEGACY_WARNED:
        return
    _LEGACY_WARNED.add(key)
    branch = gitwrap.current_branch(gitwrap.resolve(None))
    if branch and feature_mod.is_feature_branch(branch):
        err_console.print(
            f"[yellow]warning:[/yellow] deriving feature from current branch "
            f"'{branch}' is deprecated.  Pass [cyan]--feature[/cyan] explicitly."
        )


# --- Feature option reused across commands ----------------------------------

_FEATURE_OPT = typer.Option(
    None, "--feature", help="Feature name (default: infer from current branch ga/...).",
)


# --- Session lifecycle -------------------------------------------------------


@app.command()
@_catch
def init() -> None:
    """Initialize gitagent in this repository (creates .gitagent/, updates .gitignore)."""
    session_mod.init()
    console.print(
        "[green]gitagent initialized.[/green] Start a feature with "
        "[cyan]gitagent start --feature <name>[/cyan]."
    )


@app.command()
@_catch
def start(
    feature: str = _FEATURE_OPT,
    target: str = typer.Option("main", "--target", help="Branch where finalize lands commits."),
) -> None:
    """Open a session for a feature.  Creates the ga/<feature> branch if needed.

    Without --feature the current branch is used (if it is ga/...), with a
    deprecation warning.  Pass --feature explicitly to avoid the checkout dance.
    """
    _warn_legacy("start", feature)
    s = session_mod.start(feature_name=feature, target_branch=target)
    console.print(
        f"[green]Session[/green] [bold]{s.id}[/bold] [dim]({s.feature})[/dim] "
        f"at {s.base_sha[:7]} on [cyan]{s.branch}[/cyan] -> [cyan]{s.target_branch}[/cyan]."
    )


@app.command()
@_catch
def status(
    feature: str = _FEATURE_OPT,
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show session, agents, proposals, integration state, and known features.

    Without --feature a summary of all features is shown.
    With --feature the detail view for that feature is displayed.
    """
    repo = gitwrap.resolve(None)
    store.require_init(repo)

    if feature is not None:
        # detail view for one feature
        p = store.paths_for_feature(repo, feature)
        session = store.load_session(p)
        _warn_legacy("status", feature)
        if session is None:
            console.print(
                f"[yellow]No active session for feature '{feature}'.[/yellow] "
                f"Run [cyan]gitagent start --feature {feature}[/cyan] to open one."
            )
            _print_features_table(session_mod.features_summary(repo))
            return
        _print_feature_detail(p, session)
        return

    # multi-feature summary (default)
    _warn_legacy("status", feature)
    items = session_mod.features_summary(repo)
    if not items:
        console.print("[dim]No features yet. Start one with "
                      "[cyan]gitagent start --feature <name>[/cyan].[/dim]")
        return
    _print_features_table(items)


def _print_feature_detail(p: store.Paths, session: Any) -> None:
    """Print detailed view for a single feature."""
    agents: list[dict[str, Any]] = []
    for aid in store.agent_ids(p):
        try:
            a = store.load_agent(p, aid)
            agents.append(a.to_dict())
        except GitAgentError:
            continue

    proposals: list[dict[str, Any]] = []
    for pid in store.proposal_ids(p):
        try:
            prop = store.load_proposal(p, pid)
            rev = store.load_review(p, pid)
            proposals.append({"manifest": prop.to_dict(), "review": rev.to_dict()})
        except GitAgentError:
            continue

    integrated = sum(
        1
        for pid in store.proposal_ids(p)
        if store.load_review(p, pid).state.value == "integrated"
    )

    console.print(
        Panel.fit(
            f"Session [bold]{session.id}[/bold]  [dim]{session.feature}[/dim]\n"
            f"state: [cyan]{session.state.value}[/cyan]   base: {session.base_sha[:7]} "
            f"on [cyan]{session.branch}[/cyan]  target: [cyan]{session.target_branch}[/cyan]\n"
            f"integration: {integrated} applied",
            title="gitagent status",
        )
    )

    at = Table(title="Agents", show_lines=False)
    at.add_column("id", style="cyan")
    at.add_column("state")
    at.add_column("base", style="dim")
    at.add_column("branch")
    for a in agents:
        at.add_row(a["id"], a["state"], a["base_sha"][:7], a["branch"])
    if agents:
        console.print(at)

    pt = Table(title="Proposals", show_lines=False)
    pt.add_column("id", style="cyan")
    pt.add_column("agent")
    pt.add_column("state")
    pt.add_column("files", justify="right")
    pt.add_column("title")
    for pp in proposals:
        m = pp["manifest"]
        r = pp["review"]
        pt.add_row(
            m["id"],
            m["agent_id"],
            r["state"],
            str(len(m.get("files", []))),
            m["title"],
        )
    if proposals:
        console.print(pt)


def _print_features_table(features: list[dict[str, Any]]) -> None:
    if not features:
        return
    ft = Table(title="Features in this repo", show_lines=False)
    ft.add_column("branch", style="cyan")
    ft.add_column("session", style="dim")
    ft.add_column("state")
    ft.add_column("target", style="dim")
    ft.add_column("agents", justify="right")
    ft.add_column("proposals", justify="right")
    for f in features:
        ft.add_row(
            f.get("branch", "?"),
            f.get("session") or "-",
            f.get("state", "-"),
            f.get("target", "main"),
            str(f.get("agents", 0)),
            str(f.get("proposals", 0)),
        )
    console.print(ft)


@app.command(name="list-features")
@_catch
def list_features(
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List all feature branches and their session state in this repo."""
    repo = gitwrap.resolve(None)
    store.require_init(repo)
    items = session_mod.features_summary(repo)
    if json_out:
        _print_json(items)
        return
    if not items:
        console.print("[dim]No features yet. Start one with "
                      "[cyan]gitagent start --feature <name>[/cyan].[/dim]")
        return
    _print_features_table(items)


@app.command()
@_catch
def log(
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show the audit trail (append-only log.jsonl; spans all features)."""
    entries = session_mod.log_entries()
    if json_out:
        _print_json(entries)
        return
    if not entries:
        console.print("[dim]No events logged.[/dim]")
        return
    t = Table(title="Audit log", show_lines=False)
    t.add_column("ts", style="dim")
    t.add_column("event", style="cyan")
    t.add_column("detail")
    for e in entries:
        ts = e.pop("ts", "")
        event = e.pop("event", "")
        detail = " ".join(f"{k}={v}" for k, v in e.items())
        t.add_row(ts, event, detail)
    console.print(t)


@app.command()
@_catch
def abort(
    feature: str = _FEATURE_OPT,
) -> None:
    """Discard a feature's session: remove worktrees, reset .gitagent state."""
    _warn_legacy("abort", feature)
    session_mod.abort(feature_name=feature)
    console.print(
        "[yellow]Session aborted.[/yellow] Worktrees removed, .gitagent reset."
    )


# --- Agents / isolation ------------------------------------------------------


@app.command()
@_catch
def spawn(
    feature: str = _FEATURE_OPT,
    id: str = typer.Option(..., "--id", help="Agent id."),
    base: str = typer.Option(None, "--base", help="Base ref (default: session base SHA)."),
    role: str = typer.Option("", "--role", help="Role / task description."),
) -> None:
    """Create an isolated worktree for a subagent."""
    _warn_legacy("spawn", feature)
    a = agents_mod.spawn(agent_id=id, base=base, role=role, feature=feature)
    console.print(
        f"[green]Agent[/green] [bold]{a.id}[/bold] -> [dim]{a.worktree}[/dim] "
        f"[cyan]{a.branch}[/cyan]"
    )


@app.command(name="list-agents")
@_catch
def list_agents(
    feature: str = _FEATURE_OPT,
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List agents in a feature's session."""
    _warn_legacy("list-agents", feature)
    items = agents_mod.list_agents(feature=feature)
    if json_out:
        _print_json(items)
        return
    t = Table(title="Agents")
    t.add_column("id", style="cyan")
    t.add_column("state")
    t.add_column("role", style="dim")
    t.add_column("base", style="dim")
    t.add_column("branch")
    for a in items:
        t.add_row(a["id"], a["state"], a["role"], a["base_sha"][:7], a["branch"])
    if items:
        console.print(t)
    else:
        console.print("[dim]No agents spawned.[/dim]")


@app.command()
@_catch
def kill(
    id: str = typer.Argument(..., help="Agent id to remove."),
    feature: str = _FEATURE_OPT,
) -> None:
    """Remove an agent's worktree and ephemeral branch."""
    _warn_legacy("kill", feature)
    agents_mod.kill(agent_id=id, feature=feature)
    console.print(f"[yellow]Removed agent[/yellow] [bold]{id}[/bold].")


# --- Proposals ---------------------------------------------------------------


@app.command()
@_catch
def propose(
    feature: str = _FEATURE_OPT,
    agent: str = typer.Option(..., "--agent", help="Agent id making the proposal."),
    title: str = typer.Option(..., "--title", help="Short proposal title."),
    summary: str = typer.Option("", "--summary", help="Longer description."),
    confidence: float = typer.Option(None, "--confidence", help="Confidence score 0..1."),
) -> None:
    """Capture the agent's current worktree changes as a patch proposal."""
    _warn_legacy("propose", feature)
    p = proposals_mod.propose(
        agent_id=agent, title=title, summary=summary, confidence=confidence,
        feature=feature,
    )
    console.print(
        f"[green]Proposal[/green] [bold]{p.id}[/bold] by {p.agent_id} "
        f"[dim]({len(p.files)} files)[/dim]"
    )


@app.command()
@_catch
def proposals(
    feature: str = _FEATURE_OPT,
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List proposals and their review state."""
    _warn_legacy("proposals", feature)
    items = proposals_mod.list_proposals(feature=feature)
    if json_out:
        _print_json(items)
        return
    t = Table(title="Proposals")
    t.add_column("id", style="cyan")
    t.add_column("agent")
    t.add_column("state")
    t.add_column("files", justify="right")
    t.add_column("conf", justify="right")
    t.add_column("title")
    for it in items:
        m = it["manifest"]
        r = it["review"]
        conf = "" if m.get("confidence") is None else f"{m['confidence']:.2f}"
        t.add_row(
            m["id"], m["agent_id"], r["state"],
            str(len(m.get("files", []))), conf, m["title"],
        )
    if items:
        console.print(t)
    else:
        console.print("[dim]No proposals yet.[/dim]")


@app.command()
@_catch
def show(
    proposal_id: str = typer.Argument(..., help="Proposal id."),
    feature: str = _FEATURE_OPT,
) -> None:
    """Show a proposal's diff with syntax highlighting."""
    _warn_legacy("show", feature)
    patch = proposals_mod.read_patch(proposal_id=proposal_id, feature=feature)
    console.print(
        Syntax(patch, "diff", theme="ansi_dark", word_wrap=False, background_color="default")
    )


@app.command()
@_catch
def diff(
    proposal_id: str = typer.Argument(..., help="Proposal id."),
    feature: str = _FEATURE_OPT,
) -> None:
    """Print a proposal's raw diff (pipe-friendly, for LLMs)."""
    _warn_legacy("diff", feature)
    patch = proposals_mod.read_patch(proposal_id=proposal_id, feature=feature)
    sys.stdout.write(patch)


# --- Decisions / integration -------------------------------------------------


@app.command()
@_catch
def accept(
    proposal_id: str = typer.Argument(..., help="Proposal id to accept and integrate."),
    feature: str = _FEATURE_OPT,
) -> None:
    """Mark a proposal as accepted (decision only). Run `gitagent integrate` to apply it."""
    _warn_legacy("accept", feature)
    review_mod.accept(proposal_id=proposal_id, feature=feature)
    console.print(
        f"[green]Accepted[/green] [bold]{proposal_id}[/bold] "
        "[dim](decision recorded; run `gitagent integrate` to apply)[/dim]."
    )


@app.command()
@_catch
def reject(
    proposal_id: str = typer.Argument(..., help="Proposal id to reject."),
    reason: str = typer.Option("", "--reason", help="Why it is rejected."),
    feature: str = _FEATURE_OPT,
) -> None:
    """Reject a proposal (the patch is not applied)."""
    _warn_legacy("reject", feature)
    review_mod.reject(proposal_id=proposal_id, reason=reason, feature=feature)
    console.print(f"[red]Rejected[/red] [bold]{proposal_id}[/bold].")


@app.command()
@_catch
def revise(
    proposal_id: str = typer.Argument(..., help="Proposal id to send back for revision."),
    feedback: str = typer.Option(..., "--feedback", help="Feedback for the agent."),
    feature: str = _FEATURE_OPT,
) -> None:
    """Send a proposal back for another iteration."""
    _warn_legacy("revise", feature)
    review_mod.revise(proposal_id=proposal_id, feedback=feedback, feature=feature)
    console.print(f"[yellow]Sent back for revision[/yellow] [bold]{proposal_id}[/bold].")


@app.command()
@_catch
def integrate(
    feature: str = _FEATURE_OPT,
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Apply all accepted proposals onto the integration branch; detect conflicts.

    The integration worktree is reset to the current state of the target
    branch before applying proposals, so cross-feature conflicts are
    detected immediately.
    """
    _warn_legacy("integrate", feature)
    summary = review_mod.integrate(feature=feature)
    if json_out:
        _print_json(summary)
        return
    console.print(
        f"integrated: [green]{len(summary['applied'])}[/green]  "
        f"conflicts: [red]{len(summary['conflicted'])}[/red]  "
        f"already-applied: [dim]{len(summary['skipped'])}[/dim]"
    )
    if summary["conflicted"]:
        for pid in summary["conflicted"]:
            console.print(f"  [red]conflict[/red] {pid}")


# --- Finalize ----------------------------------------------------------------


@app.command()
@_catch
def finalize(
    feature: str = _FEATURE_OPT,
    message: str = typer.Option(..., "--message", "-m", help="Commit message."),
    target: str = typer.Option(None, "--target", help="Override target branch for this finalize."),
    sign: bool = typer.Option(False, "--sign", help="GPG-sign the commit."),
    keep_feature_branch: bool = typer.Option(
        False, "--keep-feature-branch",
        help="Keep the ga/<feature> branch after finalize (default: delete it).",
    ),
) -> None:
    """Produce ONE commit on the target branch (default: main) and reset .gitagent.

    Uses a detached temp worktree to avoid touching the user's checkout.
    The ga/<feature> branch is deleted after finalize unless --keep-feature-branch.
    """
    _warn_legacy("finalize", feature)
    sha = finalize_mod.finalize(
        message=message, feature=feature, target=target,
        sign=sign, keep_feature_branch=keep_feature_branch,
    )
    console.print(
        f"[green]Finalized.[/green] Single commit [bold]{sha[:7]}[/bold]. "
        "[dim](no push performed)[/dim]"
    )


@app.command(name="install-skill")
@_catch
def install_skill() -> None:
    """Install the gitagent agent skill into ~/.agents/skills/gitagent.

    The skill is bundled with the package and copied to the local opencode
    skills directory. After this, any agent in a future session that triggers
    the `gitagent` skill will see it.

    Re-run to refresh the installed copy (e.g. after upgrading gitagent).

    If the skill is not bundled (e.g. some editable installs), this command
    tells you to clone the gitagent repo and run `make install-skill`.
    """
    try:
        dst = skill_mod.install()
    except FileNotFoundError as exc:
        raise GitAgentError(str(exc)) from exc
    except FileExistsError as exc:
        raise GitAgentError(str(exc)) from exc
    console.print(f"[green]Installed gitagent skill to[/green] {dst}")
    console.print(
        "[dim]Available to agents in future sessions. "
        "Re-run `gitagent install-skill` to refresh after upgrades.[/dim]"
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
