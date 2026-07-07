#!/usr/bin/env bash
# Install the gitagent agent skill into the local opencode skills directory.
#
# Usage:
#   bash scripts/install-skill.sh
#
# The skill is copied to ~/.agents/skills/gitagent/. After this, any agent
# you run in a future session that triggers the `gitagent` skill will see it.
#
# Re-run this script to refresh the installed copy (e.g. after pulling new
# changes to the skill in this repo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL_SRC="$REPO_DIR/skills/gitagent"
SKILL_DST="${HOME}/.agents/skills/gitagent"

if [ ! -d "$SKILL_SRC" ]; then
  echo "error: skill source not found at $SKILL_SRC" >&2
  echo "Run this from inside the gitagent repository." >&2
  exit 1
fi

mkdir -p "$(dirname "$SKILL_DST")"
rm -rf "$SKILL_DST"
cp -R "$SKILL_SRC" "$SKILL_DST"
echo "Installed gitagent skill to $SKILL_DST"
echo "It will be available to agents in future sessions."
