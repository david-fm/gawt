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
from . import finalize as finalize_mod
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
    # Bypass Rich: it would soft-wrap and reinterpret `[...]` as markup.
    sys.stdout.write(_json.dumps(obj, indent=2, sort_keys=True) + "\n")


# --- Session lifecycle -------------------------------------------------------


@app.command()
@_catch
def init() -> None:
    """Initialize gitagent in this repository (creates .gitagent/, updates .gitignore)."""
    session_mod.init()
    console.print(
        "[green]gitagent initialized.[/green] Run "
        "[cyan]gitagent start --feature <name>[/cyan] to open a session."
    )


@app.command()
@_catch
def start(
    feature: str = typer.Option(..., "--feature", "-f", help="Feature name for the session."),
) -> None:
    """Open a new session anchored at the current HEAD."""
    s = session_mod.start(feature=feature)
    console.print(
        f"[green]Session[/green] [bold]{s.id}[/bold] [dim]({s.feature})[/dim] "
        f"at {s.base_sha[:7]} on [cyan]{s.base_branch}[/cyan]."
    )


@app.command()
@_catch
def status(
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show session, agents, proposals and integration state."""
    snap = session_mod.status_snapshot()
    if json_out:
        _print_json(snap)
        return

    if snap.get("session") is None:
        console.print("[yellow]No active session.[/yellow] Run [cyan]gitagent start[/cyan].")
        return

    s = snap["session"]
    console.print(
        Panel.fit(
            f"Session [bold]{s['id']}[/bold]  [dim]{s['feature']}[/dim]\n"
            f"state: [cyan]{s['state']}[/cyan]   base: {s['base_sha'][:7]} "
            f"on [cyan]{s['base_branch']}[/cyan]\n"
            f"integration: {snap['integration']['integrated_count']} applied",
            title="gitagent status",
        )
    )

    agents = snap.get("agents", [])
    at = Table(title="Agents", show_lines=False)
    at.add_column("id", style="cyan")
    at.add_column("state")
    at.add_column("base", style="dim")
    at.add_column("branch")
    for a in agents:
        at.add_row(a["id"], a["state"], a["base_sha"][:7], a["branch"])
    if agents:
        console.print(at)

    proposals = snap.get("proposals", [])
    pt = Table(title="Proposals", show_lines=False)
    pt.add_column("id", style="cyan")
    pt.add_column("agent")
    pt.add_column("state")
    pt.add_column("files", justify="right")
    pt.add_column("title")
    for p in proposals:
        m = p["manifest"]
        r = p["review"]
        pt.add_row(
            m["id"],
            m["agent_id"],
            r["state"],
            str(len(m.get("files", []))),
            m["title"],
        )
    if proposals:
        console.print(pt)


@app.command()
@_catch
def log(
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show the audit trail (append-only log.jsonl)."""
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
def abort() -> None:
    """Discard everything: remove worktrees/branches and reset .gitagent state."""
    session_mod.abort()
    console.print("[yellow]Session aborted.[/yellow] Worktrees removed, .gitagent reset.")


# --- Agents / isolation ------------------------------------------------------


@app.command()
@_catch
def spawn(
    id: str = typer.Option(..., "--id", help="Agent id."),
    base: str = typer.Option(None, "--base", help="Base ref (default: session base SHA)."),
    role: str = typer.Option("", "--role", help="Role / task description."),
) -> None:
    """Create an isolated worktree for a subagent."""
    a = agents_mod.spawn(agent_id=id, base=base, role=role)
    console.print(
        f"[green]Agent[/green] [bold]{a.id}[/bold] -> [dim]{a.worktree}[/dim] "
        f"[cyan]{a.branch}[/cyan]"
    )


@app.command(name="list-agents")
@_catch
def list_agents(
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List agents in the active session."""
    items = agents_mod.list_agents()
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
) -> None:
    """Remove an agent's worktree and ephemeral branch."""
    agents_mod.kill(agent_id=id)
    console.print(f"[yellow]Removed agent[/yellow] [bold]{id}[/bold].")


# --- Proposals ---------------------------------------------------------------


@app.command()
@_catch
def propose(
    agent: str = typer.Option(..., "--agent", help="Agent id making the proposal."),
    title: str = typer.Option(..., "--title", help="Short proposal title."),
    summary: str = typer.Option("", "--summary", help="Longer description."),
    confidence: float = typer.Option(None, "--confidence", help="Confidence score 0..1."),
) -> None:
    """Capture the agent's current worktree changes as a patch proposal."""
    p = proposals_mod.propose(
        agent_id=agent, title=title, summary=summary, confidence=confidence
    )
    console.print(
        f"[green]Proposal[/green] [bold]{p.id}[/bold] by {p.agent_id} "
        f"[dim]({len(p.files)} files)[/dim]"
    )


@app.command()
@_catch
def proposals(
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List proposals and their review state."""
    items = proposals_mod.list_proposals()
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
) -> None:
    """Show a proposal's diff with syntax highlighting."""
    patch = proposals_mod.read_patch(proposal_id=proposal_id)
    console.print(
        Syntax(patch, "diff", theme="ansi_dark", word_wrap=False, background_color="default")
    )


@app.command()
@_catch
def diff(
    proposal_id: str = typer.Argument(..., help="Proposal id."),
) -> None:
    """Print a proposal's raw diff (pipe-friendly, for LLMs)."""
    patch = proposals_mod.read_patch(proposal_id=proposal_id)
    sys.stdout.write(patch)


# --- Decisions / integration -------------------------------------------------


@app.command()
@_catch
def accept(
    proposal_id: str = typer.Argument(..., help="Proposal id to accept and integrate."),
) -> None:
    """Mark a proposal as accepted (decision only). Run `gitagent integrate` to apply it."""
    review_mod.accept(proposal_id=proposal_id)
    console.print(
        f"[green]Accepted[/green] [bold]{proposal_id}[/bold] "
        "[dim](decision recorded; run `gitagent integrate` to apply)[/dim]."
    )


@app.command()
@_catch
def reject(
    proposal_id: str = typer.Argument(..., help="Proposal id to reject."),
    reason: str = typer.Option("", "--reason", help="Why it is rejected."),
) -> None:
    """Reject a proposal (the patch is not applied)."""
    review_mod.reject(proposal_id=proposal_id, reason=reason)
    console.print(f"[red]Rejected[/red] [bold]{proposal_id}[/bold].")


@app.command()
@_catch
def revise(
    proposal_id: str = typer.Argument(..., help="Proposal id to send back for revision."),
    feedback: str = typer.Option(..., "--feedback", help="Feedback for the agent."),
) -> None:
    """Send a proposal back for another iteration."""
    review_mod.revise(proposal_id=proposal_id, feedback=feedback)
    console.print(f"[yellow]Sent back for revision[/yellow] [bold]{proposal_id}[/bold].")


@app.command()
@_catch
def integrate(
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Apply all accepted proposals onto the integration branch; detect conflicts."""
    summary = review_mod.integrate()
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
    message: str = typer.Option(..., "--message", "-m", help="Commit message."),
    sign: bool = typer.Option(False, "--sign", help="GPG-sign the commit."),
    no_reset: bool = typer.Option(
        False, "--no-reset", help="Keep .gitagent state and worktrees after committing."
    ),
) -> None:
    """Produce ONE commit on the current branch and reset .gitagent. Never pushes."""
    sha = finalize_mod.finalize(message=message, sign=sign, no_reset=no_reset)
    console.print(
        f"[green]Finalized.[/green] Single commit [bold]{sha[:7]}[/bold] on current branch. "
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
