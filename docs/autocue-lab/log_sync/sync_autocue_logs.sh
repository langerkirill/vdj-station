#!/bin/bash
# Copy the Music Sorter / AutoCue logs to the private lab repo (branch autocue-logs) so the
# cloud Claude session can read them without asking. Runs every 15 min from launchd
# (install_log_sync.sh). Read-only on the music library and on VirtualDJ's database.xml.
#
# Pushes only to langerkirill/vdj-autocue-lab (private), branch autocue-logs, from its own
# clone at ~/src/vdj-autocue-logs-sync; never touches ~/src/vdj-autocue-lab or other repos.
set -uo pipefail

SYNC="${AUTOCUE_LOG_SYNC_DIR:-$HOME/src/vdj-autocue-logs-sync}"
BRANCH="autocue-logs"
NOTES="${DJ_NOTES_ROOT:-$HOME/Music/DJ/Notes}"
DB="${VDJ_DATABASE:-$HOME/Library/Application Support/VirtualDJ/database.xml}"
OUT="$SYNC/live"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || echo /usr/bin/python3)"

echo "[$(date '+%F %T')] autocue log sync"
[ -d "$SYNC/.git" ] || { echo "no sync clone at $SYNC (run install_log_sync.sh)"; exit 1; }
cd "$SYNC" || exit 1
[ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] || { echo "sync clone is not on $BRANCH; refusing"; exit 1; }
case "$(git remote get-url origin)" in
  *langerkirill/vdj-autocue-lab*) ;;
  *) echo "origin is not the private lab repo; refusing"; exit 1 ;;
esac
git pull -q --rebase origin "$BRANCH" 2>/dev/null || true
mkdir -p "$OUT"

copy() {  # copy SRC DEST_NAME if it exists
  [ -f "$1" ] && cp -p "$1" "$OUT/$2"
}
copy "$NOTES/House-8788/music-sorter-actions.jsonl" house-8788-actions.jsonl
copy "$NOTES/House-8788/autocue_jobs.json" house-8788-autocue_jobs.json
copy "$NOTES/House-8788/deleted_markers.json" house-8788-deleted_markers.json
copy "$NOTES/music-sorter-actions.jsonl" main-8787-actions.jsonl
copy "$NOTES/autocue_jobs.json" main-8787-autocue_jobs.json
copy "$NOTES/deleted_markers.json" main-8787-deleted_markers.json

if [ -f "$DB" ]; then
  logs=()
  for f in "$OUT/house-8788-actions.jsonl" "$OUT/main-8787-actions.jsonl"; do [ -f "$f" ] && logs+=("$f"); done
  if [ "${#logs[@]}" -gt 0 ]; then
    "$PY" "$HERE/export_recent_markers.py" "$OUT/recent_markers.json" ${logs[@]+"${logs[@]}"} --database "$DB" --days 14 \
      || echo "marker export failed (logs still synced)"
  fi
fi

git add -A live
if git diff --cached --quiet; then
  echo "no changes"
  exit 0
fi
ID=()
[ -n "$(git config user.email)" ] || ID=(-c user.name="AutoCue log sync" -c user.email="autocue-log-sync@localhost")
git ${ID[@]+"${ID[@]}"} commit -q -m "live logs $(date '+%F %T %Z')" || { echo "commit failed"; exit 1; }
git push -q origin "$BRANCH" && echo "pushed" || echo "push failed (will retry next run)"
