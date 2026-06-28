#!/bin/bash
#
# Install (or reinstall) the weekly refresh as a macOS launchd agent.
#
# Usage: scripts/install-weekly.sh <SET_CODE> [HOUR] [MINUTE] [WEEKDAY]
#   SET_CODE   set to refresh, e.g. MSH
#   HOUR       0-23, default 9
#   MINUTE     0-59, default 0
#   WEEKDAY    0-7 (1=Mon ... 0/7=Sun), default 1 (Monday)
#
# Prereq: run `uv run mtg-eval <SET>` once interactively first so the OAuth token
# exists, and publish your OAuth app so the token does not expire (see README).
# macOS only. On Linux, use cron instead (see README).

set -euo pipefail

SET="${1:?usage: install-weekly.sh <SET_CODE> [HOUR] [MINUTE] [WEEKDAY]}"
HOUR="${2:-9}"
MINUTE="${3:-0}"
WEEKDAY="${4:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER="$SCRIPT_DIR/weekly-update.sh"
LABEL="com.mtg-eval.weekly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/.config/mtg-eval"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
chmod +x "$WORKER"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$WORKER</string>
        <string>$SET</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key><integer>$WEEKDAY</integer>
        <key>Hour</key><integer>$HOUR</integer>
        <key>Minute</key><integer>$MINUTE</integer>
    </dict>
    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>$LOG_DIR/launchd.out.log</string>
    <key>StandardErrorPath</key><string>$LOG_DIR/launchd.err.log</string>
</dict>
</plist>
PLIST_EOF

plutil -lint "$PLIST" >/dev/null
UID_N=$(id -u)
launchctl bootout "gui/$UID_N" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_N" "$PLIST"

echo "Installed $LABEL: refreshes $SET every weekday=$WEEKDAY at $HOUR:$(printf '%02d' "$MINUTE")."
echo "Test it now:   launchctl kickstart -k gui/$UID_N/$LABEL"
echo "Watch the log: tail -f $LOG_DIR/weekly.log"
echo "Uninstall:     launchctl bootout gui/$UID_N $PLIST"
