# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed (BREAKING) — v0.6.2
- **Rule A: writing a file you never read is `STALE_WRITE`.** An existing file with no `last_reads` row for the writing agent is treated as a potential clobber: `write`, `edit`, and `delete` are refused with `STALE_WRITE`. The file's creator (its own `last_reads` row is set at creation) can always rewrite it; any other agent must `read_file` first. Closes the race where two agents created the same file sequentially and the second silently overwrote the first.
- Tests updated to the new discipline (read before touching a file owned by someone else).

### Changed (BREAKING) — v0.6.1
- **`expected_sha256` removed from the MCP API.** Agents handled it badly (schema required a string, callers passed `"null"`/`""` sentinels). gawt now tracks each agent's **last read per file** in a new `last_reads` table and validates writes against it automatically, rejecting with `STALE_WRITE` when the disk changed since that read. Agents never pass or see a SHA. The row is updated after write/edit and cleared after delete.
- **`read_file` is readable: no `diff`.** Returns `content`, `sha256`, `base_sha`, `edits[]` (with `op`, resolved `role`, `intent` text, `intent_id`, `ts`), and `warning`. The git `diff` now lives only in `snapshot_status` (whole-worktree view). Intent + role already appear in the read **and** in the `STALE_WRITE`/lock rejection payload, so the coordinator no longer needs a separate `list_edits` call.
- **Stale-read notice.** Reading a file whose last read no longer matches the current disk content returns a short `note`.
- `list_edits` resolves `intent` text and `role` (JOIN).
- Schema migration `user_version` 3 → 4: `CREATE TABLE last_reads(agent_id, file, sha256, ts, PRIMARY KEY(agent_id, file))`.

## [0.6.0] - 2026-08-20
- **Inbox removed.** `check_inbox` / `send_message` / `inbox.py` are gone. Coordination now emerges from the **pheromone** (the `edits` log), not messages.
- **Per-file write locks.** `write_file` / `edit_file` / `delete_file` acquire a lock first and always release it in `finally`. A fresh foreign lock → **informed rejection** (`{status: "rejected", read: {...}}`), never applied. No waiting in the server.
- **Reads are informed (git-style).** Same `read_file` tool now returns `content`, `sha256`, `path`, `base_sha`, `diff`, `edits[]`, and an intent `warning`. No diff toggle — the git diff is always present.
- **Partial snapshots multi-orchestrator.** `finalize_session` removed. New `snapshot_session` commits part of the shared worktree onto the target branch via a temp worktree, without deleting the live worktree. Per-file `snapshot_progress` maintains the frontier per session.
- **Multi-session.** Several sessions share ONE worktree. `start_session` reuses it; `abort_session(session_id)` removes it only when no other session is open.
- **Target branch fixed per worktree.** The first session picks it; later ones ignore a new `target_branch`.
- **Crash reconciliation.** Disk is the source of truth: `snapshot_status` inserts synthetic `{op: "adjusted"}` rows for disk changes with no pheromone entry, so replay never raises `REPLAY_MISMATCH` on crash residue.
- **Schema migration `user_version` 2 → 3.** Dropped `inbox` and `idx_one_open_session`; added `replace_all` to `edits`, `lock_ttl_seconds` to `session`, and new `snapshot_progress` / `locks` / `snapshots` tables.
- New tools: `snapshot_session`, `snapshot_status`, `list_snapshots`, `list_sessions`.
- Removed tool: `finalize_session`.
- `list_edits` gains `limit` (row cap for boundary picking).
- Removed: `inbox.py`, `tests/test_inbox.py`. Added: `locks.py`, `replay.py`, `snapshot.py`, `tests/test_locks.py`, `test_replay.py`, `test_snapshot.py`, `test_status.py`, `test_reconcile.py`.

## [0.4.2] - 2026-07-29

### Added
- **`propose` works from inside an agent's worktree.** Subagents no longer need to `cd` to the main repo to submit a proposal. The main repo is resolved via `git rev-parse --git-common-dir`, so this works from any linked worktree, subdirectory, or the worktree itself.
- **Automatic `--feature` / `--agent` inference in `propose`.** When a subagent runs `propose` from inside `.gitagent/features/<key>/agents/<id>/worktree/` (or any subdirectory), `feature` and `agent_id` are inferred from the cwd if they are not passed on the command line. Explicit flags always win. The inference emits a one-line notice to stderr naming the resolved feature/agent so silent mistakes are visible.
- `gitwrap.git_common_dir()` and `gitwrap.main_repo_root()` helpers for resolving the shared `git` directory and the main repo root from any cwd (linked worktree, subdir, or main checkout).
- 5 new tests in `tests/test_proposals.py` covering inference from the worktree, from a subdir, failure from the integration worktree, failure from the main repo, and explicit-override-beats-inference.

### Changed
- `proposals.propose()` now accepts `agent_id: str | None` (was: required) and an optional `cwd: Path | None` for tests; defaults to `Path.cwd()`.
- `propose` audit log entries gain an `inferred` boolean for observability.

### Notes
- Only `propose` infers from cwd. Every other command (`init`, `start`, `spawn`, `kill`, `proposals`, `show`, `diff`, `accept`, `reject`, `revise`, `integrate`, `finalize`) still requires explicit `--feature` and must be run from the main repo. This keeps the supervisor's surface explicit and avoids silently writing to the wrong feature from a stray cwd.
- Integration worktrees deliberately fail inference (no `agents/<id>/meta.json` under their cwd) so the supervisor cannot accidentally propose into its own integration worktree.

## [0.4.1] - 2026-07-29

### Fixed
- Quoted `SKILL.md` frontmatter `description` to fix YAML parsing on stricter parsers.



### Changed (BREAKING)
- **gitagent is now fully decoupled from the user's Git branches.** It no longer creates, checks out, or deletes any branch in your repository (`ga/<feature>`, `agent/<id>/<sid>`, or `gitagent/integration/...`). Every command requires `--feature <name>` (a logical key, not a Git branch). Your local checkout stays on `main` throughout.
- **Agent isolation uses detached worktrees.** `spawn` gives each subagent a detached `git worktree` derived from `main`; no ephemeral branch is created, so agents can never pollute or switch your refs.
- **`finalize` lands one commit on `main`** via a detached temp worktree + `git update-ref`. It never touches your checkout and never created a branch to delete.
- **Removed the `--keep-feature-branch` flag** — there is no feature branch to keep anymore.
- **`--feature` is now required** on every command. The current-branch inference (and its deprecation warning) was removed entirely.
- **`integrate` applies onto a detached integration worktree** (reset to the live target before applying). Cross-feature conflicts surface immediately via 3-way merge.
- **Concurrency**: `finalize` now takes a per-feature `fcntl` lock so two concurrent finalizes (or a finalize racing an integrate) cannot both issue `update-ref` and clobber each other's commit on `main`.

### Added
- `store.lock(p, "finalize")` guards the single commit per feature.
- New tests asserting the repo's branch list stays `["main"]` across the full lifecycle (start → spawn → propose → accept → integrate → finalize) and that the user remains on `main`.

### Migration from v0.3.0
- `gitagent start --feature x` (no branch, no checkout) — unchanged, but `--feature` is now mandatory.
- `gitagent spawn --feature x --id a1` — agent now gets a detached worktree; no `agent/...` branch.
- `gitagent finalize --feature x -m "..."` — lands one commit on `main`; `--keep-feature-branch` no longer exists.
- Your repository only ever has `main` plus whatever branches you create yourself. gitagent will never add or remove one.

## [0.3.0] - 2026-07-17

### Changed (BREAKING)
- **`finalize` lands on `main` directly** (configurable via `--target`). A `ga/<feature>` branch was still created at `start` and deleted after finalize (use `--keep-feature-branch` to preserve it). The user's local checkout was never disturbed — a detached temp worktree was used.
- **`--feature` option on all commands**. Feature identity was decoupled from the current branch. Without `--feature`, the current branch was used as default with a deprecation warning.
- **`integrate` resets the integration worktree to the live target branch** before applying proposals. Cross-feature conflicts surfaced immediately via 3-way merge, not at merge-to-main time.
- **`status` showed all features by default** (equivalent to `list-features`). With `--feature`, showed the detailed view for one feature.
- **`Session` model gained `target_branch`** field (default: `"main"`).

### Added
- `feature.coerce()` and `feature.branch_for_feature()` for branch-name normalization without requiring a checkout.
- `store.paths_for_feature(repo, name)` resolved paths by feature name, independent of the current branch.
- `gitwrap.worktree_add_detached()` for creating detached temp worktrees.
- `gitwrap.reset_hard()`, `gitwrap.update_ref()` for plumbing operations.
- 8 new tests in `tests/test_multi_feature.py` covering branchless flow, cross-feature isolation, `--keep-feature-branch`, and the deprecation warning.

### Migration from v0.2.0
- `git checkout -b ga/x && gitagent start` → `gitagent start --feature x` (no checkout needed).
- `gitagent finalize -m "..."` → `gitagent finalize --feature x -m "..."` (lands on main directly).
- After finalize, no manual merge to main was needed — it was already on main.
- `gitagent status` now showed all features. Use `--feature x` for the detail view.
- The current branch was still accepted as a default for `--feature` (with a warning). Pass `--feature` explicitly to avoid the warning.

## [0.2.0] - 2026-07-13

### Changed (BREAKING)
- **Multi-feature model**: a feature is now a git branch whose name starts with `ga/`. The current branch determines the active session. Two features in two branches run in parallel without colliding.
- **`start` no longer takes `--feature`**. The feature name is derived from the current branch (`ga/auth-rl` → `auth-rl`). `start` refuses to run on `main` / `master` / detached HEAD.
- **`finalize` lands the commit on the current feature branch**, not on `main`. The superagent merges feature branches into `main` with normal git (PR, `git merge --squash`, etc.). `gitagent` never touches `main`.
- **Storage layout**: per-feature state moved to `.gitagent/features/<key>/` (one subdirectory per feature). The audit log remains global at `.gitagent/log.jsonl`.

### Added
- New command `gitagent list-features [--json]` to inspect every feature branch and its session state.
- `Session` model gains `branch` and `feature_key` fields.
- New module `gitagent.feature` for branch-slug derivation (`ga/<name>` → safe directory key).
- 7 new tests in `tests/test_multi_feature.py` covering parallel features, isolation, branch-preservation, and the `start`-on-main guard.
- README and `SKILL.md` updated with the multi-feature workflow and revised "where things live" layout.

### Migration from v0.1.0
- `gitagent start --feature "x"` → `git checkout -b ga/x && gitagent start`.
- After `finalize`, manually merge the feature branch to `main`:
  `git checkout main && git merge --squash ga/x && git commit -m "..."`.
- Per-feature worktrees now live under `.gitagent/features/<key>/agents/<id>/worktree` (instead of `.gitagent/agents/<id>/worktree`).

## [0.1.0] - 2026-07-07

### Added
- Initial public release.
- `git worktree`-based agent isolation under `.gitagent/`.
- Session lifecycle: `init`, `start`, `status`, `log`, `abort`.
- Agent management: `spawn`, `list-agents`, `kill`.
- Proposals as patch + manifest: `propose`, `proposals`, `show`, `diff`.
- Superagent decisions: `accept`, `reject`, `revise`, `integrate` (with `git apply --3way` conflict detection).
- `finalize` producing a single squashed commit on the current branch and resetting `.gitagent` (never pushes).
- `--json` output on `status`, `log`, `list-agents`, `proposals`, `integrate` for LLM/orchestrator consumption.
- Append-only audit trail at `.gitagent/log.jsonl`.
- CI (ruff + pytest on Python 3.11–3.13) and trusted-publishing release workflow.
- Bundled `gitagent` agent skill (installable via `gitagent install-skill` or `make install-skill`).

### Notes
- The PyPI distribution is published as **`gawt`** (not `gitagent`) because the
  name `gitagent` on PyPI is already taken by an unrelated project (a Tornado
  HTTP webhook server, last released 2016). The installed command is still
  `gitagent`. Install with `pipx install gawt` / `uv tool install gawt`.
