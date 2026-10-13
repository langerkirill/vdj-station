#!/bin/bash
# One-time setup: keep AutoCue on the newest TESTED version from GitHub, on its own.
#
#   ~/src/vdj-autocue-lab/docs/autocue-lab/autoupdate/install_autoupdate.sh
#   ... install_autoupdate.sh --house ~/src/vdj-station-house   # House checkout (default shown)
#   ... install_autoupdate.sh --uninstall                       # stop updating; files stay
#
# Creates ~/.vdj-autocue (releases/, current, model/, state/, bin/), installs the newest
# released version of the vdj-autocue package (GitHub Release v<version>) as the first release with
# the rule vdj-autocue>=<that version> (no upper bound), puts Kirill's newest cue model in
# model/, and installs ~/Library/LaunchAgents/com.vdj-autocue.update.plist (a check every 15
# minutes and at login). Then start the sorter with  ~/.vdj-autocue/bin/run_sorter.sh 8788.
# Running it again keeps the installed versions, the rule and the model.
set -euo pipefail

LABEL="com.vdj-autocue.update"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP="${AUTOCUE_HOME:-$HOME/.vdj-autocue}"
HERE="$(cd "$(dirname "$0")" && pwd -P)"
CANON="$(cd "$HERE/../../.." && pwd -P)"
LOG="$HOME/Library/Logs/autocue-update.log"
ERR="$HOME/Library/Logs/autocue-update.err"

HOUSE_ARG=""; PY_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall)
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
      rm -f "$PLIST"
      echo "AutoCue auto-update stopped. $APP stays (a sorter started with run_sorter.sh keeps"
      echo "working on the installed version). Remove it with: rm -rf $APP"
      exit 0 ;;
    --house) HOUSE_ARG="$2"; shift 2 ;;
    --python) PY_ARG="$2"; shift 2 ;;
    *) echo "usage: $0 [--house DIR] [--python PY] [--uninstall]"; exit 2 ;;
  esac
done
# a re-run keeps the House checkout and Python chosen before, unless new ones are given
HOUSE="${HOUSE_ARG:-$(cat "$APP/state/house" 2>/dev/null || echo "$HOME/src/vdj-station-house")}"
HOUSE="$(cd "$HOUSE" && pwd -P)" || { echo "No House checkout at $HOUSE (use --house)."; exit 1; }
[ -d "$HOUSE/ui/tests" ] || { echo "$HOUSE has no ui/tests; is it the House sorter checkout? (use --house)"; exit 1; }
PY="$PY_ARG"
[ -n "$PY" ] || [ -n "$HOUSE_ARG" ] || PY="$(cat "$APP/state/python" 2>/dev/null || true)"
[ -n "$PY" ] || PY="$HOUSE/venv/bin/python"

case "$(git -C "$CANON" remote get-url origin 2>/dev/null)" in
  *langerkirill/vdj-autocue-lab*) ;;
  *) echo "$CANON is not a checkout of the private lab repo; stopping."; exit 1 ;;
esac
[ -x "$PY" ] || { echo "No Python at $PY (use --python)."; exit 1; }
if ! "$PY" -m pytest --version >/dev/null 2>&1; then
  echo "Updates are tested before they install, which needs pytest in $PY:"
  echo "  $PY -m pip install pytest"
  echo "Then run this again."
  exit 1
fi

mkdir -p "$APP"/{bin,releases,model,state,gate} "$(dirname "$PLIST")" "$(dirname "$LOG")"
printf '%s\n' "$CANON" > "$APP/state/checkout"
printf '%s\n' "$HOUSE" > "$APP/state/house"
printf '%s\n' "$PY" > "$APP/state/python"
for f in autocue_update.sh autocue_gate.py autocue_versions.py autocue_github.py run_sorter.sh; do   # new file + rename: a running
  cp "$HERE/$f" "$APP/bin/.$f.new" && chmod +x "$APP/bin/.$f.new" && mv -f "$APP/bin/.$f.new" "$APP/bin/$f"
done                                                            # copy keeps its old inode

# --- first release: the newest GitHub Release (needs this Mac's GitHub login: gh auth login) ---
"$APP/bin/autocue_update.sh" --bootstrap

# --- the shared cue model: the newest one Kirill has (never an empty file) ------------------
"$PY" - "$APP/model" "$CANON/vdj_cuer/ml/artifacts" "$HOUSE/vdj_cuer/ml/artifacts" \
    "$(cd "$APP/current" && pwd -P)/vdj_cuer/ml/artifacts" <<'PY'
import json, os, shutil, sys
from pathlib import Path

def info(d):
    d = Path(d)
    j, m = d / "cue_bar_clf.json", d / "cue_bar_clf.joblib"
    if not (m.is_file() and m.stat().st_size > 0 and j.is_file()):
        return None
    try:
        return str(json.loads(j.read_text()).get("saved_at") or "")
    except Exception:
        return None

shared, sources = Path(sys.argv[1]), sys.argv[2:]
have = info(shared)
best = max(((info(s), s) for s in sources if info(s) is not None), default=None)
if best and (have is None or best[0] > have):
    src = Path(best[1])
    shared.mkdir(parents=True, exist_ok=True)
    if have is not None:  # keep the one being replaced
        backup = shared / "backup"
        backup.mkdir(exist_ok=True)
        stamp = have.replace(":", "").replace("+", "_")[:24]
        shutil.copy2(shared / "cue_bar_clf.joblib", backup / f"cue_bar_clf.{stamp}.joblib")
        shutil.copy2(shared / "cue_bar_clf.json", backup / f"cue_bar_clf.{stamp}.json")
    tmp = shared / ".cue_bar_clf.joblib.tmp"
    shutil.copy2(src / "cue_bar_clf.joblib", tmp)
    os.replace(tmp, shared / "cue_bar_clf.joblib")
    shutil.copy2(src / "cue_bar_clf.json", shared / "cue_bar_clf.json")
    print(f"Cue model: {src} (saved {best[0]}) -> {shared}")
elif have is not None:
    print(f"Cue model: keeping {shared} (saved {have})")
else:
    print("Cue model: none found; AutoCue runs without it until one is trained")
PY

# --- the earlier updater (staging worktree in the checkout, state under Caches) -------------
OLD="$HOME/Library/Caches/vdj-autocue/update"
if [ -d "$OLD" ]; then
  git -C "$CANON" worktree remove --force "$OLD/staging" 2>/dev/null || true
  git -C "$CANON" worktree prune 2>/dev/null || true
  rm -rf "$OLD"
  echo "Removed the earlier updater's files ($OLD)."
fi

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$APP/bin/autocue_update.sh</string></array>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>Nice</key><integer>15</integer>
  <key>LowPriorityIO</key><true/>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>$ERR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>AUTOCUE_HOME</key><string>$APP</string>
  </dict>
</dict>
</plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo
echo "AutoCue auto-update is on (a check every 15 minutes; log: $LOG)."
echo "Start the House sorter so it follows updates:"
echo "  $APP/bin/run_sorter.sh 8788"
echo "Status: $APP/bin/autocue_update.sh --status    Back one version: --rollback"
echo "Before a gig: --pause (tests use CPU while they run)    After: --resume"
