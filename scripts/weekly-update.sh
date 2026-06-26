#!/bin/bash
#
# Weekly MSH refresh. Pulls fresh Scryfall + 17Lands data, recomputes scores,
# and syncs the <SET>_Scores Google Sheet (your my_eval / my_notes are preserved).
#
# Driven by a launchd agent (see scripts/com.mtg-eval.mtg-eval.weekly.plist).
# Runs non-interactively, so the OAuth token at ~/.config/mtg-eval/token.json
# must already exist and the OAuth app should be Published (see README) so the
# refresh token does not expire.
#
# To track a different set later, change SET below (or pass it as $1).

set -euo pipefail

SET="${1:-MSH}"
REPO="/Users/user/personal/mtg-limited-eval"
UV="/opt/homebrew/bin/uv"
LOG_DIR="$HOME/.config/mtg-eval"
LOG="$LOG_DIR/weekly.log"

mkdir -p "$LOG_DIR"
cd "$REPO"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') refreshing $SET =====" >> "$LOG"

if "$UV" run mtg-eval "$SET" --refresh >> "$LOG" 2>&1; then
  echo "refresh ok" >> "$LOG"
else
  code=$?
  echo "refresh exited with code $code" >> "$LOG"
fi

# Best-effort: commit and push the refreshed CSV so git keeps a weekly history.
# Never let a git hiccup fail the data update.
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
