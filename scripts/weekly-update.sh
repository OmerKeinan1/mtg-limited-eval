#!/bin/bash
#
# Weekly refresh for one set. Pulls fresh Scryfall + 17Lands + expert-grade data,
# recomputes scores, and syncs the <SET>_Scores Google Sheet (your my_eval /
# my_notes are preserved). Then best-effort commits the CSV.
#
# Usage: weekly-update.sh <SET_CODE>
# Driven by a launchd agent (see scripts/install-weekly.sh). Runs
# non-interactively, so the OAuth token at ~/.config/mtg-eval/token.json must
# already exist and the OAuth app should be Published (see README) so the refresh
# token does not expire.

set -euo pipefail

SET="${1:?usage: weekly-update.sh <SET_CODE>}"

# Locate the repo from this script's own path, so it works wherever it's cloned.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"

# Find uv (launchd has a minimal PATH).
UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  for p in /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.local/bin/uv"; do
    [ -x "$p" ] && UV="$p" && break
  done
fi
[ -z "$UV" ] && { echo "uv not found on PATH" >&2; exit 1; }

LOG_DIR="$HOME/.config/mtg-eval"
LOG="$LOG_DIR/weekly.log"
mkdir -p "$LOG_DIR"
cd "$REPO"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') refreshing $SET =====" >> "$LOG"
if "$UV" run mtg-eval "$SET" --refresh >> "$LOG" 2>&1; then
  echo "refresh ok" >> "$LOG"
else
  echo "refresh exited with code $?" >> "$LOG"
fi

# Best-effort: commit + push the refreshed CSV for git history. Never let a git
# hiccup fail the data update.
{
  git add "evaluations/${SET}.csv" 2>/dev/null || true
  if ! git diff --cached --quiet 2>/dev/null; then
    git commit -q -m "Weekly ${SET} data refresh ($(date '+%Y-%m-%d'))" 2>/dev/null || true
    git push -q 2>/dev/null || true
    echo "committed + pushed CSV" >> "$LOG"
  else
    echo "no CSV changes to commit" >> "$LOG"
  fi
} || true

echo "" >> "$LOG"
