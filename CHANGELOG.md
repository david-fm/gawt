# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
