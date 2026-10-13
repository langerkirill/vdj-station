#!/bin/bash
# Run a Music Sorter on the installed AutoCue, and move it to new versions automatically.
#
#   ~/.vdj-autocue/bin/run_sorter.sh 8788             # House sorter, write mode
#   ~/.vdj-autocue/bin/run_sorter.sh 8788 --readonly  # House sorter, look only
#   ~/.vdj-autocue/bin/run_sorter.sh 8787             # main station (needs station-8787-switch.patch)
#
# Each start resolves ~/.vdj-autocue/current to its release folder, so the sorter and every
# AutoCue job it runs use one version from start to stop. When the updater installs a newer
# version it stops this sorter, only while /api/busy reports no background job, no AutoCue
# process runs and nothing was edited for 10 minutes; this loop then starts it on the new
# version. If a new version makes the sorter stop right at start 3 times in a row, this starts
# it on the version it last ran on (without touching "current"); once that has run 30 s the
# new version is rolled back (autocue_update.sh --rollback-from; never reinstalled). If the old
# version stops too, the cause is elsewhere and nothing is rolled back.
# Ctrl+C here (or closing the window) stops it for good.
set -u
APP="${AUTOCUE_HOME:-$HOME/.vdj-autocue}"
PORT="${1:-8788}"
MODE="${2:-}"
HOUSE="$(cat "$APP/state/house" 2>/dev/null || echo "$HOME/src/vdj-station-house")"
STATION="${VDJ_STATION_ROOT:-$HOME/src/vdj-station}"
case "$PORT" in
  8788) SCRIPT="$HOUSE/ui/start_8788_write.sh"; [ "$MODE" = "--readonly" ] && SCRIPT="$HOUSE/ui/start_8788.sh" ;;
  8787) SCRIPT="$STATION/ui/start_8787_noreload.sh" ;;
  *) echo "usage: $0 8788|8787 [--readonly]"; exit 2 ;;
esac
grep -q VDJ_AUTOCUE_ROOT "$SCRIPT" 2>/dev/null || {
  echo "$SCRIPT cannot use the installed AutoCue yet; run docs/autocue-lab/mac_setup.sh --apply first."; exit 1; }
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "Port $PORT is already in use (pid $(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t | head -1)). Stop that sorter first."
  exit 1
fi
REC="$APP/state/run_sorter.$PORT"
UI_DIR="$(cd "$(dirname "$SCRIPT")" && pwd -P)"   # this sorter's UI: AutoCue's training labels read it
mkdir -p "$APP/state"
short() { basename "$1" | cut -c1-7; }

STOP=0
child=""
# TERM, not INT: uvicorn treats a second INT (Ctrl+C reaches it too) as "quit without
# finishing requests"; TERM lets in-flight requests finish.
trap 'STOP=1; [ -n "$child" ] && kill -TERM "$child" 2>/dev/null' INT TERM HUP
quick=0      # stops within 30 s of a start, in a row
good=""      # the last release this sorter ran on for at least 30 s
bad=""       # a new release it stopped on right after start 3 times: run on $good meanwhile
while [ "$STOP" = 0 ]; do
  current="$(cd "$APP/current" 2>/dev/null && pwd -P)" || { echo "AutoCue is not installed (install_autoupdate.sh)."; break; }
  [ -n "$bad" ] && [ "$current" != "$bad" ] && bad=""   # rolled back, or a newer version: try it
  root="$current"
  [ -n "$bad" ] && root="$good"
  echo "[run_sorter] $PORT on AutoCue $(short "$root")  ($root)"
  started=$(date +%s)
  VDJ_AUTOCUE_ROOT="$root" VDJ_AUTOCUE_MODEL_DIR="$APP/model" VDJ_SORTER_UI_DIR="$UI_DIR" \
    AUTOCUE_AUTORESTART=1 "$SCRIPT" &
  child=$!
  # the updater stops only this server pid, only while this wrapper ($$) is alive, only when idle;
  # the UI field tells it this wrapper passes VDJ_SORTER_UI_DIR on every restart
  printf '%s\t%s\t%s\t%s\n' "$child" "$root" "$$" "$UI_DIR" > "$REC"
  if [ -n "$bad" ]; then
    # the previous version runs: once it has run 30 s, the new one is to blame; roll it back
    # (only while it is still current; waits for a running update check to finish)
    ( sleep 30; kill -0 "$child" 2>/dev/null \
        && "$APP/bin/autocue_update.sh" --rollback-from "$(basename "$bad")" >/dev/null 2>&1 ) &
  fi
  wait "$child"
  wait "$child" 2>/dev/null                      # a trap interrupts the first wait
  child=""
  rm -f "$REC"
  [ "$STOP" = 1 ] && break
  if [ $(( $(date +%s) - started )) -lt 30 ]; then
    quick=$((quick + 1))
  else
    quick=0; good="$root"
  fi
  if [ "$quick" -ge 3 ] && [ -z "$bad" ] && [ -n "$good" ] && [ "$root" != "$good" ]; then
    bad="$root"; quick=0
    echo "[run_sorter] $PORT stopped right after start 3 times on the new AutoCue $(short "$bad");"
    echo "[run_sorter] starting it on $(short "$good"), the version it last ran on; if that runs, $(short "$bad") is rolled back."
  fi
  if [ "$quick" -ge 6 ]; then
    echo "[run_sorter] $PORT stopped right after start 6 times in a row; not restarting (see the messages above)."
    osascript -e 'display notification "Sorter keeps stopping at start; see its Terminal window." with title "AutoCue"' >/dev/null 2>&1
    break
  fi
  echo "[run_sorter] $PORT stopped; starting it again in 3 s (Ctrl+C to stop)"
  sleep 3
done
rm -f "$REC"
echo "[run_sorter] $PORT stopped."
