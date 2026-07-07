# gitagent

> Agent workspace manager over Git: isolate, review and consolidate multi-agent changes into a **single commit**.

`gitagent` is a lightweight CLI that wraps `git worktree` so a **superagent** can spawn isolated **subagents**, collect their work as **proposals** (patch + manifest), accept/reject/revise them, and finally produce **one clean commit** on the real repository — then clean everything up. It never pushes.

Two planes are kept strictly separate:

- **`.git`** — the source of truth. Untouched until `finalize`, which writes exactly one commit.
- **`.gitagent/`** — an ephemeral coordination layer: worktrees, patches, manifests, decisions, audit log. Discardable and reset after `finalize`/`abort`.

`gitagent` does **not** reimplement Git. Proposals are stored as `git diff` patches plus JSON metadata, never as a second history.

## Decision / application split (Option B)

`gitagent` cleanly separates **deciding** from **applying**:

| Command | Role |
|---|---|
| `accept <pid>` | Record the decision "approved". Does **not** apply. |
| `reject <pid>` | Record "rejected". |
| `revise <pid>` | Send back to the agent (new `pid` on re-propose). |
| `integrate` | **Apply** all accepted proposals onto the integration branch. Detects conflicts. |
| `finalize` | Calls `integrate` (if needed) + creates **one** commit on the current branch. |

So a minimal flow is: `accept` each → `finalize`. Run `integrate` standalone when you want to see conflicts *before* the final commit.

## Install

The PyPI distribution is **`gawt`** (the name `gitagent` on PyPI is a different,
unrelated project). The command it installs is still called `gitagent`.

```bash
# end user (isolated environment)
pipx install gawt
# or
uv tool install gawt

# from GitHub
pipx install "git+https://github.com/david-fm/gawt"

# local development
git clone https://github.com/david-fm/gawt && cd gawt
uv tool install . -e   # or: pipx install -e .
```

Requires Python 3.11+ and a working `git` on `PATH`.

## Quick start (Option B)

```bash
gitagent init
gitagent start --feature "auth-rate-limiting"

# each subagent gets an isolated worktree + ephemeral branch
gitagent spawn --id a_backend --role "implement limiter"
gitagent spawn --id a_tests   --role "write tests"

# subagents work in their own folders, then propose (no commit to .git)
gitagent propose --agent a_backend --title "Token bucket limiter" --confidence 0.85
gitagent propose --agent a_tests   --title "Limiter tests"        --confidence 0.9

# superagent reviews and marks decisions
gitagent proposals
gitagent show p_7f3a
gitagent accept p_7f3a
gitagent revise p_9b2c --feedback "add edge cases"
# ... a_tests re-proposes (a new pid is generated) ...
gitagent accept p_9b2c

# OPTIONAL: pre-flight apply to surface conflicts before committing
gitagent integrate

# consolidate into ONE commit on the current branch, then reset
gitagent finalize --message "feat(auth): add rate limiting with tests"
# → 1 local commit on HEAD, .gitagent reset, no push
```

## Commands

| Command | Description |
| --- | --- |
| `init` | Create `.gitagent/` and add it to `.gitignore`. |
| `start --feature <name>` | Open a session anchored at current `HEAD`. |
| `status [--json]` | Show session, agents, proposals, integration state. |
| `log [--json]` | Append-only audit trail (`log.jsonl`). |
| `abort` | Discard everything: remove worktrees/branches, reset `.gitagent`. |
| `spawn --id <id> [--base <ref>] [--role ...]` | Create an isolated worktree for a subagent. |
| `list-agents [--json]` | List agents. |
| `kill <id>` | Remove an agent's worktree and ephemeral branch. |
| `propose --agent <id> --title ... [--summary ...] [--confidence 0..1]` | Capture worktree changes as a patch proposal. |
| `proposals [--json]` | List proposals + review state. |
| `show <id>` | Show a proposal's diff (color). |
| `diff <id>` | Print a proposal's raw diff (pipe-friendly, for LLMs). |
| `accept <id>` | Mark a proposal as accepted (decision only; does **not** apply). |
| `reject <id> [--reason ...]` | Reject a proposal (patch not applied). |
| `revise <id> --feedback ...` | Send a proposal back for another iteration. |
| `integrate [--json]` | Apply all accepted proposals onto the integration branch; detect conflicts. |
| `finalize --message ... [--sign] [--no-reset]` | Call `integrate` + produce one commit on the current branch + reset `.gitagent`. **Never pushes.** |
| `install-skill` | Install the bundled `gitagent` agent skill into `~/.agents/skills/gitagent`. |

## How it works

- **Isolation**: `git worktree add` gives every agent its own `HEAD`, index and working tree while sharing the object store — ideal for parallelism.
- **Proposals as patches**: `git -C <worktree> diff --cached <base_sha> --binary` is stored as `change.patch` and re-applied with `git apply --3way` on the integration worktree. Agent "commits" never pollute the final history.
- **Integration order = proposal creation order**: the first agent to `propose` integrates first. Plan who proposes first if their change is the "base" others depend on.
- **Single commit**: `finalize` does `git merge --squash <integration-branch>` on the current branch and creates exactly one commit, then removes all worktrees/branches and resets `.gitagent` (the audit log is kept).
- **Conflicts**: a conflicting patch is marked `conflict` at `integrate` time — the session stays alive so the superagent can `revise`, fix the integration worktree manually (then re-`accept` + re-`integrate`), or `abort`.
- **Concurrency**: file locks (`fcntl`) guard review-state writes; `accept` is safe to race (it only marks).
- **Audit**: every event is appended to `.gitagent/log.jsonl` (parseable by another agent/LLM).
- **Dual output**: humans get Rich tables; `--json` and `diff` give raw, pipe-friendly output for orchestration.

## Layout

```
.gitagent/
├── session.json                 # active session: id, feature, base_sha, state
├── agents/<agent-id>/
│   ├── meta.json                # role, worktree path, ephemeral branch, state
│   └── worktree/                # git worktree for this agent
├── proposals/<proposal-id>/
│   ├── manifest.json            # agent, base_sha, files, summary, confidence
│   ├── change.patch             # git diff of the proposal
│   └── review.json              # pending|accepted|integrated|rejected|revise|conflict + feedback
├── integration/worktree/        # integration branch under construction
├── locks/                       # fcntl lockfiles for concurrent writes
└── log.jsonl                    # append-only audit trail
```

Proposal states: `pending → accepted → integrated | conflict | revise | rejected`.
Session states: `open → integrating → finalized | aborted`.

## Installing the agent skill

`gitagent` ships with a **skill** so any AI agent can use it freely — it teaches another agent the full CLI surface, the Option B decision/application split, conflict resolution recipes, and the supervisor/coder/reviewer patterns.

The skill lives in the repo at `skills/gitagent/` (SKILL.md + AGENTS.md) and gets installed into the local opencode skills directory so future agent sessions can load it.

### From a gitagent install (pipx / pip / uv tool)

```bash
gitagent install-skill
# → installs bundled skill to ~/.agents/skills/gitagent
# → re-run to refresh after upgrading gitagent
```

### From a git clone (developers / source installs)

```bash
git clone https://github.com/david-fm/gawt
cd gitagent
make install-skill
# (equivalent to: bash scripts/install-skill.sh)
```

### Manual

```bash
cp -R skills/gitagent ~/.agents/skills/
```

The skill will be available to agents in **future** sessions. Re-run any of the above commands to refresh the installed copy.

## Design principle

> `finalize` **never** runs `git push`. It produces the local commit and stops. Pushing is entirely the user's responsibility.

## License

MIT © David Florez Mazuera
