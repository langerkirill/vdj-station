#!/bin/bash
# One-time setup on the Mac: sync the sorter / AutoCue logs to the private lab repo every
# 15 minutes so the cloud Claude session can read them on its own.
#
#   ~/src/vdj-autocue-lab/docs/autocue-lab/log_sync/install_log_sync.sh
#   ~/src/vdj-autocue-lab/docs/autocue-lab/log_sync/install_log_sync.sh --uninstall
#
# What it creates: a separate clone ~/src/vdj-autocue-logs-sync (branch autocue-logs) and
# ~/Library/LaunchAgents/com.vdj-autocue.log-sync.plist. Log: ~/Library/Logs/autocue-log-sync.log
set -euo pipefail

LABEL="com.vdj-autocue.log-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HERE="$(cd "$(dirname "$0")" && pwd)"
SYNC="${AUTOCUE_LOG_SYNC_DIR:-$HOME/src/vdj-autocue-logs-sync}"
REPO_URL="$(git -C "$HERE" remote get-url origin)"
BRANCH="autocue-logs"
LOG="$HOME/Library/Logs/autocue-log-sync.log"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Log sync stopped and removed. The clone $SYNC is left in place (delete it if you like)."
  exit 0
fi

case "$REPO_URL" in
  *langerkirill/vdj-autocue-lab*) ;;
  *) echo "This checkout's origin is $REPO_URL, not the private lab repo; stopping."; exit 1 ;;
esac

# 1. Own clone on the logs branch (never the working checkout).
if [ ! -d "$SYNC/.git" ]; then
  git clone -q --depth 1 --branch main "$REPO_URL" "$SYNC"
  if git -C "$SYNC" ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    git -C "$SYNC" fetch -q --depth 1 origin "$BRANCH:$BRANCH"
    git -C "$SYNC" checkout -q "$BRANCH"
  else
    git -C "$SYNC" checkout -q -b "$BRANCH"
  fi
fi
# The scripts run from the sync clone's own copy of this folder.
mkdir -p "$SYNC/sync-tools"
cp "$HERE/sync_autocue_logs.sh" "$HERE/export_recent_markers.py" "$SYNC/sync-tools/"
chmod +x "$SYNC/sync-tools/sync_autocue_logs.sh"

# 2. First run now (shows any git / permission problem right away).
"$SYNC/sync-tools/sync_autocue_logs.sh"

# 3. launchd agent: every 15 minutes and at login.
mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$SYNC/sync-tools/sync_autocue_logs.sh</string></array>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
</dict>
</plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed: logs sync every 15 minutes to branch $BRANCH. Log: $LOG"
echo "Stop any time: $HERE/install_log_sync.sh --uninstall"
