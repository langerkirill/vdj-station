#!/bin/bash
# AutoCue auto-update for the Mac: the newest TESTED version of the vdj-autocue package.
#
#   ~/.vdj-autocue/bin/autocue_update.sh                    # one check (launchd: every 15 min + login)
#   ~/.vdj-autocue/bin/autocue_update.sh --status
#   ~/.vdj-autocue/bin/autocue_update.sh --pin 'vdj-autocue>=1.4.0'   # the version rule (pip style)
#   ~/.vdj-autocue/bin/autocue_update.sh --rollback         # back one version; never reinstall this one
#   ~/.vdj-autocue/bin/autocue_update.sh --pause | --resume
#   ~/.vdj-autocue/bin/autocue_update.sh --refresh-updater  # these scripts, from the installed release
#   (install_autoupdate.sh runs --bootstrap; run_sorter.sh runs --rollback-from <version>)
#
# Versions are the GitHub Releases v<version> of the private repo (semantic versioning, see
# docs/autocue-lab/RELEASING.md), each with the wheel, a test bundle and SHA256SUMS, built by
# .github/workflows/release.yml from the tag. Downloads use the GitHub login already on this Mac
# (gh, or git's stored login) and are checked against SHA256SUMS; a version whose release ever
# changes is ignored. The rule (state/requirement; default vdj-autocue>=<first installed>, no
# upper bound) decides which versions may install: the newest one it allows, when newer.
#
# Layout (~/.vdj-autocue):
#   releases/<version>/ the package as pip installs it (--target), never changed after install
#   current             link to the active release (switched atomically)
#   dist/<version>/     that version's downloaded release assets (the gate's base next time)
#   model/              the one cue model every release loads and 8788 retrains
#                       (VDJ_AUTOCUE_MODEL_DIR); it keeps Kirill's corrections across versions
#   state/ bin/         bookkeeping, these scripts (the test gate works in
#                       ~/Library/Caches/vdj-autocue-gate)
# A sorter started with run_sorter.sh resolves "current" once at start and hands that folder to
# every AutoCue job it runs, so switching "current" never changes code under a running server
# or job.
#
# One check: list the releases. Nothing else happens while VirtualDJ is open. For the version
# the rule picks: download its assets, check the House Python has what it needs, and test its
# INSTALLED package against the installed version's with autocue_gate.py (AutoCue's tests + the
# House sorter's ui/tests, JUnit XML; a crash, timeout or any new failure refuses it); install it
# with pip into releases/<version> and switch "current". A sorter that runs under run_sorter.sh
# on an older release is then restarted, only when /api/busy says no background job runs
# (checked twice), no AutoCue process runs, and nothing was edited for 10 minutes. Any other
# sorter gets one notification that a restart loads the new version. Never writes database.xml.
set -uo pipefail

# The updater's own version: raise it whenever any updater script changes. --refresh-updater
# installs the scripts of the installed release only when theirs is higher (never older ones).
UPDATER_VERSION=3

APP="${AUTOCUE_HOME:-$HOME/.vdj-autocue}"
STATE="$APP/state"
REL="$APP/releases"
DIST="$APP/dist"
CUR="$APP/current"
# scratch for the test gate: not in a hidden folder (the House library scan skips .dot paths,
# which would quietly skip some tests) and not under /tmp (the House read-only guard allows it)
GATE="${AUTOCUE_GATE_DIR:-$HOME/Library/Caches/vdj-autocue-gate}"
LOG="${AUTOCUE_UPDATE_LOG:-$HOME/Library/Logs/autocue-update.log}"
CHECKOUT="$(cat "$STATE/checkout" 2>/dev/null)"
HOUSE="$(cat "$STATE/house" 2>/dev/null)"
PY="$(cat "$STATE/python" 2>/dev/null)"
NOTES="${DJ_NOTES_ROOT:-$HOME/Music/DJ/Notes}"
IDLE_MINUTES="${AUTOCUE_IDLE_MINUTES:-10}"
RECHECK_SECONDS="${AUTOCUE_RECHECK_SECONDS:-15}"
SCRIPTS="autocue_update.sh autocue_gate.py autocue_versions.py autocue_github.py run_sorter.sh"
VERSIONS="$APP/bin/autocue_versions.py"
GH="$APP/bin/autocue_github.py"
REQ="$STATE/requirement"
TAB=$'\t'
export PIP_DISABLE_PIP_VERSION_CHECK=1
mkdir -p "$STATE" "$REL" "$DIST" "$(dirname "$LOG")"
touch "$STATE/skip" "$STATE/history" "$STATE/versions" "$STATE/moved"

log() {
  local line="[$(date '+%F %T')] $*"
  echo "$line" >> "$LOG"
  [ -t 1 ] && echo "$line"
  return 0
}
rotate_log() {
  local size
  [ -f "$LOG" ] || return 0
  size="$(wc -c < "$LOG" 2>/dev/null || echo 0)"
  if [ "${size// /}" -gt 1000000 ] 2>/dev/null; then
    tail -n 3000 "$LOG" > "$LOG.tmp" && mv -f "$LOG.tmp" "$LOG"
  fi
  return 0
}
notify() {  # macOS banner (text passed as an argument, never spliced into AppleScript)
  command -v osascript >/dev/null 2>&1 && osascript - "$1" >/dev/null 2>&1 <<'OSA'
on run argv
  display notification (item 1 of argv) with title "AutoCue"
end run
OSA
  log "notify: $1"
}
notify_once() {  # notify_once <key> <message>: at most once per key
  [ -f "$STATE/notified.$1" ] && return 0
  : > "$STATE/notified.$1"
  notify "$2"
}
with_timeout() {  # with_timeout <seconds> cmd...  (macOS has no timeout(1))
  local secs="$1"; shift
  perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$secs" "$@"
}
realdir() { (cd "$1" 2>/dev/null && pwd -P); }
cur_ver() { local d; d="$(realdir "$CUR")" && basename "$d"; }   # a version (a commit: first updater)
is_version() { case "$1" in [0-9]*.*) return 0 ;; *) return 1 ;; esac; }
short() { printf '%s' "$1" | cut -c1-7; }
label() { if is_version "$1"; then printf '%s' "$1"; else printf 'commit %s' "$(short "$1")"; fi; }
set_current() {  # set_current <release name>: new link next to "current", then rename over it
  "$PY" - "$REL/$1" "$CUR" <<'PYCODE'
import os, sys
target, link = sys.argv[1], sys.argv[2]
tmp = link + ".tmp"
if os.path.lexists(tmp):
    os.unlink(tmp)
os.symlink(target, tmp)
os.replace(tmp, link)
PYCODE
}
wheel_of() { echo "$DIST/$1/vdj_autocue-$1-py3-none-any.whl"; }
bundle_of() { echo "$DIST/$1/vdj-autocue-$1-tests.tar.gz"; }
assets() {  # assets <version>: its release assets in dist/<version>, downloaded once and checked
  # (state/versions keeps the checksum of each version's SHA256SUMS: wheel and tests together)
  local d="$DIST/$1" sha known
  [ -s "$(wheel_of "$1")" ] && [ -s "$(bundle_of "$1")" ] && return 0
  rm -rf "$d.part"
  sha="$(with_timeout 1800 "$PY" "$GH" download "$1" "$d.part" 2>>"$LOG")" || { rm -rf "$d.part"; return 1; }
  known="$(awk -v v="$1" '$1 == v {print $2; exit}' "$STATE/versions")"
  if [ -n "$known" ] && [ "$known" != "$sha" ]; then   # a version never changes
    rm -rf "$d.part"
    grep -qxF "$1" "$STATE/moved" || echo "$1" >> "$STATE/moved"
    notify_once "moved.$1" "Release v$1 on GitHub was changed after it came out; version $1 is ignored (a version never changes)."
    return 1
  fi
  [ -n "$known" ] || echo "$1 $sha" >> "$STATE/versions"
  rm -rf "$d" && mv "$d.part" "$d"
}
gate_tree() {  # gate_tree <version> <dir>: the version's tests around its INSTALLED package
  rm -rf "$2" && mkdir -p "$2" || return 1
  tar -xzf "$(bundle_of "$1")" -C "$2" || return 1
  "$PY" -m pip install -q --no-deps --no-index --target "$2" "$(wheel_of "$1")" >>"$LOG" 2>&1
}
legacy_tree() {  # legacy_tree <commit> <dir>: a first-updater release's source tree (checkout)
  [ -n "$CHECKOUT" ] || return 1
  rm -rf "$2" && mkdir -p "$2" || return 1
  git -C "$CHECKOUT" archive "$1" -- . ':(exclude)bench/improve-partial' \
      | tar -x -C "$2"
}
smoke() {  # smoke <dir> <version>: the installed package imports, is that version, has its model
  ( cd / && PYTHONPATH="$1" "$PY" - "$2" "$1" <<'PYCODE'
import os, sys, importlib.metadata as m
import vdj_database_safety, vdj_cue_patch, vdj_cuer, vdj_cuer.cue_writer, vdj_cuer.autocue_job
import vdj_cuer.ml.model as M
got = m.version("vdj-autocue")
assert got == sys.argv[1] == vdj_cuer.__version__, (got, sys.argv[1], vdj_cuer.__version__)
assert os.path.realpath(vdj_cuer.__file__).startswith(os.path.realpath(sys.argv[2]))
assert M.SHIPPED_ARTIFACT.is_file() and M.SHIPPED_ARTIFACT.stat().st_size > 0
PYCODE
  ) >>"$LOG" 2>&1
}
install_release() {  # install_release <version>: releases/<version> from its wheel, once
  local d="$REL/$1"
  [ -f "$d/.autocue_version" ] && return 0
  rm -rf "$d.tmp"
  "$PY" -m pip install -q --no-deps --no-index --target "$d.tmp" "$(wheel_of "$1")" >>"$LOG" 2>&1 \
    || { rm -rf "$d.tmp"; return 1; }
  "$PY" -m compileall -q "$d.tmp" >/dev/null 2>&1   # bytecode now: jobs never write into it
  echo "$1" > "$d.tmp/.autocue_version"
  smoke "$d.tmp" "$1" || { log "the installed package of $1 does not load (see above)"; rm -rf "$d.tmp"; return 1; }
  rm -rf "$d" && mv "$d.tmp" "$d"
}
install_version() {  # install_version <version>: install it and make it current
  install_release "$1" && set_current "$1" || return 1
  { echo "$1"; cat "$STATE/history" 2>/dev/null || true; } | awk 'NF && !seen[$0]++' > "$STATE/history.tmp" \
    && mv -f "$STATE/history.tmp" "$STATE/history"
}
list_versions() {  # "<version> <release id>" for every published release (GitHub)
  with_timeout 120 "$PY" "$GH" list 2>>"$LOG"
}
rule() { local r; r="$(cat "$REQ" 2>/dev/null)"; printf '%s' "${r:-vdj-autocue}"; }
pick_version() {  # pick_version <release list>: the version the rule allows and that is newer
  local cur curv excl="$STATE/exclude.list" f
  cur="$(cur_ver)"; curv="$cur"
  is_version "$cur" || curv=""   # the first updater's release: any version is newer
  { cat "$STATE/skip" "$STATE/moved"
    for f in "$STATE"/rejected.*; do   # refused in the last 6 h (tried again after that)
      [ -f "$f" ] && [ -z "$(find "$f" -mmin +360 2>/dev/null)" ] && echo "${f##*/rejected.}"
    done; } > "$excl" 2>/dev/null
  printf '%s\n' "$1" | "$PY" "$VERSIONS" pick --spec "$(rule)" --current "$curv" --exclude "$excl" | cut -f1
}
vdj_running() {  # the release's own check (executable name, never a command line)
  ( cd "$(realdir "$CUR")" 2>/dev/null && env -u PYTHONPATH "$PY" -c \
      'import sys; from vdj_database_safety import is_virtualdj_running as r; sys.exit(0 if r() else 1)' ) 2>/dev/null
}

# --- one run at a time for update, rollback and refresh (status/pause/resume never wait) ----
LOCK="$STATE/lock"
take_lock() {
  if mkdir "$LOCK" 2>/dev/null; then echo $$ > "$LOCK/pid"; return 0; fi
  local pid; pid="$(cat "$LOCK/pid" 2>/dev/null)"
  if [ -n "$pid" ] && ps -o command= -p "$pid" 2>/dev/null | grep -q autocue_update; then
    if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null)" ]; then
      notify_once "lock.$pid" "An AutoCue update check (pid $pid) has run for over 3 hours; see $LOG"
    fi
    return 1
  fi
  if [ -z "$pid" ] && [ -z "$(find "$LOCK" -maxdepth 0 -mmin +1 2>/dev/null)" ]; then
    return 1  # just created by another run that has not written its pid yet
  fi
  # stale (crash, reboot, or the pid now belongs to something else): one run takes it over
  if mv "$LOCK" "$LOCK.stale.$$" 2>/dev/null; then
    if [ "$(cat "$LOCK.stale.$$/pid" 2>/dev/null)" != "$pid" ]; then
      # another run broke the stale lock first and this moved its fresh one: put it back
      mv "$LOCK.stale.$$" "$LOCK" 2>/dev/null || rm -rf "$LOCK.stale.$$"
      return 1
    fi
    rm -rf "$LOCK.stale.$$"
  fi
  mkdir "$LOCK" 2>/dev/null && echo $$ > "$LOCK/pid"
}
wait_lock() {  # wait_lock <seconds>: take the lock, waiting for a running check to finish
  local deadline=$(( $(date +%s) + $1 ))
  until take_lock; do
    [ "$(date +%s)" -lt "$deadline" ] || return 1
    sleep 5
  done
}
drop_lock() { [ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LOCK"; return 0; }

# --- servers, jobs, idleness ----------------------------------------------------------------
server_pid() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1; }
proc_env() {  # proc_env <pid> <NAME>: NAME's value in that process's environment (best effort)
  local flags="eww"; [ "$(uname)" = Darwin ] && flags="-E -ww"
  # shellcheck disable=SC2086
  ps $flags -o command= -p "$1" 2>/dev/null | tr ' ' '\n' | sed -n "s/^$2=//p" | head -1
}
server_root() {  # server_root <port> <pid>: "<release dir><TAB>auto|manual"
  local rec="$STATE/run_sorter.$1" pid root wrapper uidir
  if [ -f "$rec" ]; then  # run_sorter.sh: the server pid, its release, its own pid, the sorter UI
    IFS="$TAB" read -r pid root wrapper uidir < "$rec"
    # an older run_sorter.sh (no UI field) would restart the sorter without VDJ_SORTER_UI_DIR
    if [ "$pid" = "$2" ] && [ -n "$wrapper" ] && [ -n "$uidir" ] && kill -0 "$wrapper" 2>/dev/null; then
      printf '%s\tauto\n' "$root"; return
    fi
  fi
  printf '%s\tmanual\n' "$(proc_env "$2" VDJ_AUTOCUE_ROOT)"
}
autocue_processes() {  # any AutoCue work in progress (8787, 8788, trial script, CLI)
  pgrep -f 'vdj_cuer[./](autocue_job|ml[./]ingest|ml[./]cli)|grid_verify_worker|automatic_music_cuer_gemini' 2>/dev/null
}
api_busy() {  # api_busy <port>: 0 idle; 1 busy or unsure; 2 this sorter has no /api/busy
  local code
  code="$(curl -sS -o "$STATE/busy.$1.json" -w '%{http_code}' --max-time 10 \
      "http://127.0.0.1:$1/api/busy" 2>/dev/null)"
  [ "$code" = 404 ] && return 2
  [ "$code" = 200 ] || return 1
  "$PY" -c 'import json, sys; sys.exit(0 if json.load(open(sys.argv[1])).get("busy") is False else 1)' \
      "$STATE/busy.$1.json" 2>/dev/null || return 1
  return 0
}
sorter_idle() {  # sorter_idle <port>: 0 only when every check says idle, twice; 2 = cannot tell
  local r actions
  [ -z "$(autocue_processes)" ] || return 1
  api_busy "$1"; r=$?; [ "$r" -eq 0 ] || return "$r"
  actions="$NOTES/House-8788/music-sorter-actions.jsonl"
  [ "$1" = 8787 ] && actions="$NOTES/music-sorter-actions.jsonl"
  [ -n "$(find "$actions" -mmin -"$IDLE_MINUTES" 2>/dev/null)" ] && return 1
  sleep "$RECHECK_SECONDS"   # again: work that was just starting shows up now
  [ -z "$(autocue_processes)" ] || return 1
  api_busy "$1" || return 1
  return 0
}

restart_step() {
  vdj_running && return 0   # never during a gig (also after a rollback)
  local want; want="$(realdir "$CUR")" || return 0
  local appdir; appdir="$(realdir "$APP")"
  local c; c="$(label "$(cur_ver)")"
  for port in 8788 8787; do
    local pid info root how r
    pid="$(server_pid "$port")"; [ -n "$pid" ] || continue
    info="$(server_root "$port" "$pid")"; root="${info%%"$TAB"*}"; how="${info#*"$TAB"}"
    case "$root" in
      "$CUR"|"$CUR/"|"$appdir/current"|"$appdir/current/")
        notify_once "currentlink.$port.$pid" "Sorter $port was started on the 'current' link itself; restart it with run_sorter.sh $port so updates never change code under it."
        continue ;;
      "$REL"/*|"$appdir/releases"/*) ;;
      *) continue ;;  # not on an auto-updated AutoCue
    esac
    [ "$(realdir "$root")" = "$want" ] && continue
    if [ "$how" != auto ]; then
      notify_once "restart.$port.$c" "AutoCue $c installed. Restart $port to use it: stop it and start $APP/bin/run_sorter.sh $port (then it follows updates on its own)."
      continue
    fi
    sorter_idle "$port"; r=$?
    if [ "$r" -eq 0 ]; then
      log "restarting $port (pid $pid) to load AutoCue $c"
      kill -TERM "$pid" 2>/dev/null
      notify "Sorter $port restarted on AutoCue $c."
    elif [ "$r" -eq 2 ]; then
      notify_once "nobusy.$port.$c" "AutoCue $c installed. Restart $port to use it (automatic restarts need the House busy check: mac_setup.sh --apply)."
    else
      log "$port restarts once idle (a job, an AutoCue process, or edits in the last $IDLE_MINUTES min)"
    fi
  done
}

model_step() {  # the shared model takes the shipped one only when that one is newer (a shared
                # model the code cannot use falls back to the shipped one when loading)
  local shipped; shipped="$(realdir "$CUR")/vdj_cuer/ml/artifacts"
  local shared="$APP/model"
  # temp files of saves that were killed (ingest timeout) and never renamed
  find "$shared" -maxdepth 1 -name '.cue_bar_clf.*.tmp' -mmin +120 -exec rm -f {} + 2>/dev/null
  [ -s "$shipped/cue_bar_clf.joblib" ] && [ -f "$shipped/cue_bar_clf.json" ] || return 0
  mkdir -p "$shared"
  if "$PY" - "$shipped/cue_bar_clf.json" "$shared/cue_bar_clf.json" "$shared/cue_bar_clf.joblib" <<'PYCODE'
import json, os, sys
def meta(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}
ship, have_meta = meta(sys.argv[1]), meta(sys.argv[2])
have = os.path.getsize(sys.argv[3]) > 0 if os.path.isfile(sys.argv[3]) else False
newer = str(ship.get("saved_at") or "") > str(have_meta.get("saved_at") or "")
sys.exit(0 if (not have or newer) else 1)
PYCODE
  then
    if [ -s "$shared/cue_bar_clf.joblib" ]; then  # keep the replaced one (5 newest backups)
      local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
      mkdir -p "$shared/backup"
      cp -p "$shared/cue_bar_clf.joblib" "$shared/backup/cue_bar_clf.$stamp.joblib"
      cp -p "$shared/cue_bar_clf.json" "$shared/backup/cue_bar_clf.$stamp.json" 2>/dev/null
      ls -1t "$shared/backup"/cue_bar_clf.*.joblib 2>/dev/null | tail -n +6 | while read -r f; do
        rm -f "$f" "${f%.joblib}.json"
      done
    fi
    cp -p "$shipped/cue_bar_clf.joblib" "$shared/.cue_bar_clf.joblib.tmp" \
      && mv -f "$shared/.cue_bar_clf.joblib.tmp" "$shared/cue_bar_clf.joblib" \
      && cp -p "$shipped/cue_bar_clf.json" "$shared/cue_bar_clf.json" \
      && log "shared cue model: took the newer model shipped with $(label "$(cur_ver)") (old one in model/backup)"
  fi
}

cleanup_releases() {  # keep current, the 3 before it, and any release a process still uses
  local keep used name d
  keep="$( { cur_ver; head -4 "$STATE/history" 2>/dev/null; } )"
  used="$( { cat "$STATE"/run_sorter.* 2>/dev/null
             for p in $(pgrep -if python 2>/dev/null); do
               proc_env "$p" VDJ_AUTOCUE_ROOT
               lsof -a -p "$p" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'
             done; } | sed -n 's#.*/releases/\([^/[:space:]]*\).*#\1#p' | sort -u)"
  for d in "$REL"/*; do
    [ -d "$d" ] || continue
    name="$(basename "$d")"
    case "$name" in *.tmp) [ -n "$(find "$d" -maxdepth 0 -mmin +120 2>/dev/null)" ] && rm -rf "$d"; continue ;; esac
    printf '%s\n' "$keep" | grep -qxF "$name" && continue
    printf '%s\n' "$used" | grep -qxF "$name" && continue
    rm -rf "$d" && log "removed old release $(label "$name")"
  done
  for d in "$DIST"/*; do  # downloads: the kept releases' (a future gate base) and fresh ones
    [ -d "$d" ] || continue
    name="$(basename "$d")"
    [ -d "$REL/$name" ] && continue
    [ -n "$(find "$d" -maxdepth 0 -mmin +1440 2>/dev/null)" ] && rm -rf "$d"
  done
}

updater_scripts_of() {  # the updater scripts in the installed release's test bundle (or nothing)
  local c; c="$(cur_ver)"
  is_version "$c" && [ -s "$(bundle_of "$c")" ] && echo "$(bundle_of "$c")"
}
updater_stamp() {  # updater_stamp <autocue_update.sh text on stdin>: its UPDATER_VERSION (0 if none)
  sed -n 's/^UPDATER_VERSION=\([0-9][0-9]*\).*/\1/p' | head -1 | grep . || echo 0
}
bundle_stamp() {  # the UPDATER_VERSION of the installed release's updater (0 if none)
  local b; b="$(updater_scripts_of)" || { echo 0; return; }
  tar -xzOf "$b" docs/autocue-lab/autoupdate/autocue_update.sh 2>/dev/null | updater_stamp
}
updater_step() {  # tell once when the installed release carries a NEWER updater (higher stamp)
  local have new
  have="$(updater_stamp < "$APP/bin/autocue_update.sh")"; new="$(bundle_stamp)"
  [ "$new" -gt "$have" ] 2>/dev/null || return 0
  notify_once "updater.$new" "AutoCue $(cur_ver) comes with a newer updater. To use it: $APP/bin/autocue_update.sh --refresh-updater"
}

rollback() {  # rollback: current -> the newest earlier release not skipped; current is skipped
  local now prev s
  now="$(cur_ver)"
  prev=""
  for s in $(grep -vxF "$now" "$STATE/history"); do
    grep -qxF "$s" "$STATE/skip" && continue
    [ -f "$REL/$s/vdj_database_safety.py" ] && { prev="$s"; break; }
  done
  [ -n "$prev" ] || { echo "No earlier version is installed; nothing changed."; return 1; }
  set_current "$prev" || { echo "Could not switch; nothing changed."; return 1; }
  echo "$now" >> "$STATE/skip"
  { echo "$prev"; grep -vxF -e "$now" -e "$prev" "$STATE/history" || true; } > "$STATE/history.tmp" \
    && mv -f "$STATE/history.tmp" "$STATE/history"
  log "rolled back $(label "$now") -> $(label "$prev"); $(label "$now") will not be installed again"
  echo "Back on $(label "$prev"). $(label "$now") is never reinstalled; newer versions still install."
  restart_step
  return 0
}

status() {
  local c; c="$(cur_ver)"
  if [ -z "$c" ]; then echo "AutoCue auto-update is not installed (install_autoupdate.sh)."; return; fi
  echo "installed AutoCue: $(label "$c")"
  echo "  folder: $(realdir "$CUR")"
  local list allowed newest
  list="$(list_versions 2>/dev/null)"
  allowed="$(printf '%s\n' "$list" | "$PY" "$VERSIONS" pick --spec "$(rule)" --current "" | cut -f1)"
  newest="$(printf '%s\n' "$list" | "$PY" "$VERSIONS" pick --spec "vdj-autocue" --current "" | cut -f1)"
  echo "  rule: $(rule)   newest it allows: ${allowed:-none}   newest on GitHub: ${newest:-none}"
  local earlier; earlier="$(grep -vxF "$c" "$STATE/history" 2>/dev/null | head -3 | tr '\n' ' ')"
  [ -n "${earlier// /}" ] && echo "  earlier versions kept: $earlier(--rollback goes to the first)"
  if [ -f "$STATE/paused" ]; then echo "updates: PAUSED (--resume starts them again)"; else echo "updates: on (every 15 minutes)"; fi
  [ -f "$STATE/last_check" ] && echo "  last check: $(cat "$STATE/last_check")"
  [ -s "$STATE/skip" ] && echo "  never reinstalled (rolled back): $(tr '\n' ' ' < "$STATE/skip")"
  local f; for f in "$STATE"/rejected.*; do
    [ -f "$f" ] && echo "  refused by the tests: ${f##*/rejected.}: $(head -1 "$f")"
  done
  [ -f "$STATE/waiting" ] && echo "  $(cat "$STATE/waiting")"
  [ -f "$STATE/fetch_failing_since" ] && echo "  GitHub unreachable since $(date -r "$(cat "$STATE/fetch_failing_since")" '+%F %T' 2>/dev/null || cat "$STATE/fetch_failing_since")"
  local port pid info root how
  for port in 8788 8787; do
    pid="$(server_pid "$port")"
    if [ -z "$pid" ]; then echo "  $port: not running"; continue; fi
    info="$(server_root "$port" "$pid")"; root="${info%%"$TAB"*}"; how="${info#*"$TAB"}"
    if [ -z "$root" ]; then echo "  $port: not on the installed AutoCue (start it with run_sorter.sh $port)"
    elif [ "$root" = "$CUR" ] || [ "$root" = "$CUR/" ]; then
      echo "  $port: started on the 'current' link itself; restart it with run_sorter.sh $port"
    elif [ "$(realdir "$root")" = "$(realdir "$CUR")" ]; then echo "  $port: on the installed version"
    else
      echo -n "  $port: on $(label "$(basename "$root")"); a restart loads $(label "$c")"
      [ "$how" = auto ] && echo " (automatic when idle)" || echo
    fi
  done
  echo "  log: $LOG"
}

# an updater refreshed from an older one may lack the helper scripts: take them from the
# installed release's bundle, or from the git checkout of a first-updater install
for helper in autocue_versions.py autocue_github.py; do
  [ -s "$APP/bin/$helper" ] && continue
  b="$(updater_scripts_of)"
  if [ -n "$b" ]; then
    tar -xzOf "$b" "docs/autocue-lab/autoupdate/$helper" > "$APP/bin/.$helper.new" 2>/dev/null
  elif [ -n "$CHECKOUT" ]; then
    git -C "$CHECKOUT" show "origin/main:docs/autocue-lab/autoupdate/$helper" > "$APP/bin/.$helper.new" 2>/dev/null
  fi
  [ -s "$APP/bin/.$helper.new" ] && mv -f "$APP/bin/.$helper.new" "$APP/bin/$helper" || rm -f "$APP/bin/.$helper.new"
done

case "${1:-}" in
  --status) status; exit 0 ;;
  --pause) : > "$STATE/paused"; log "paused"; echo "AutoCue updates paused (installed version stays)."; exit 0 ;;
  --resume) rm -f "$STATE/paused"; log "resumed"; echo "AutoCue updates resumed."; exit 0 ;;
  --pin)
    norm="$("$PY" "$VERSIONS" check-spec "${2:-}")" || { echo "Not a valid rule: '${2:-}' (e.g. 'vdj-autocue>=1.4.0' or '>=1.4,<2')"; exit 2; }
    printf '%s\n' "$norm" > "$REQ.tmp" && mv -f "$REQ.tmp" "$REQ"
    log "rule set to $norm"
    echo "Rule: $norm. The next check installs the newest version it allows (after the tests)."
    c="$(cur_ver)"
    if is_version "$c" && ! "$PY" "$VERSIONS" allows "$norm" "$c"; then
      echo "Note: this rule does not allow the installed $c, so the next check moves to the newest"
      echo "version it allows, even an older one (after the tests). Your cue model stays."
    fi
    exit 0 ;;
  --rollback)
    take_lock || { echo "An update check is running right now (pid $(cat "$LOCK/pid" 2>/dev/null)); try again in a few minutes."; exit 1; }
    trap drop_lock EXIT
    rollback; exit $? ;;
  --rollback-from)  # run_sorter.sh: <version> made the sorter stop at start; only if still current
    bad="${2:-}"
    [ -n "$bad" ] || { echo "usage: $0 --rollback-from <version>"; exit 2; }
    wait_lock 600 || { log "rollback from $(label "$bad") gave up waiting for a running check"; exit 1; }
    trap drop_lock EXIT
    if [ "$(cur_ver)" != "$bad" ]; then
      log "rollback from $(label "$bad") not needed: $(label "$(cur_ver)") is current"; exit 0
    fi
    log "sorter stopped right after start on $(label "$bad") 3 times and ran on the previous version"
    rollback; exit $? ;;
  --refresh-updater)
    take_lock || { echo "An update check is running right now; try again in a few minutes."; exit 1; }
    trap drop_lock EXIT
    b="$(updater_scripts_of)"
    [ -n "$b" ] || { echo "The installed release has no updater scripts; nothing changed."; exit 1; }
    have="$(updater_stamp < "$APP/bin/autocue_update.sh")"; new="$(bundle_stamp)"
    if [ "$new" -lt "$have" ] 2>/dev/null; then
      echo "The installed updater ($have) is newer than AutoCue $(cur_ver)'s ($new); nothing changed."; exit 0
    fi
    for f in $SCRIPTS; do   # all read and checked first, then each renamed into place
      tar -xzOf "$b" "docs/autocue-lab/autoupdate/$f" > "$APP/bin/.$f.new" 2>/dev/null \
        && [ -s "$APP/bin/.$f.new" ] \
        && case "$f" in
             *.sh) bash -n "$APP/bin/.$f.new" ;;
             *.py) "$PY" -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$APP/bin/.$f.new" ;;
           esac \
        || { rm -f "$APP/bin"/.*.new; echo "Could not read or check $f in AutoCue $(cur_ver); nothing changed."; exit 1; }
    done
    for f in $SCRIPTS; do chmod +x "$APP/bin/.$f.new" && mv -f "$APP/bin/.$f.new" "$APP/bin/$f"; done
    log "updater scripts refreshed from AutoCue $(cur_ver)"
    echo "Updater scripts refreshed from AutoCue $(cur_ver)."
    exit 0 ;;
  --bootstrap)  # install_autoupdate.sh: the first version (the newest release), when none is installed
    take_lock || { echo "An update check is running right now; try again in a few minutes."; exit 1; }
    trap drop_lock EXIT
    if [ -n "$(cur_ver)" ]; then echo "AutoCue $(label "$(cur_ver)") is installed already."; exit 0; fi
    "$PY" "$GH" check 2>>"$LOG" || { echo "Cannot read the AutoCue releases on GitHub (log in on this Mac: gh auth login). See $LOG"; exit 1; }
    ver="$(pick_version "$(list_versions)")"
    [ -n "$ver" ] || { echo "No released version on GitHub matches $(rule)."; exit 1; }
    echo "Installing AutoCue $ver ..."
    assets "$ver" || { echo "Could not download release v$ver (see $LOG)."; exit 1; }
    if ! missing="$("$PY" "$VERSIONS" check-deps "$(wheel_of "$ver")")"; then
      echo "AutoCue $ver needs, in $PY:"; printf '  %s\n' "$missing"
      echo "Install them ($PY -m pip install ...) and run the installer again."; exit 1
    fi
    install_version "$ver" || { echo "Could not install $ver (see $LOG)."; exit 1; }
    [ -s "$REQ" ] || echo "vdj-autocue>=$ver" > "$REQ"
    log "installed $ver as the first version (rule: $(rule))"
    echo "Installed AutoCue $ver. Rule: $(rule)."
    exit 0 ;;
  "") ;;
  *) echo "usage: $0 [--status|--pin RULE|--rollback|--pause|--resume|--refresh-updater]"; exit 2 ;;
esac

# --- one update check ---------------------------------------------------------------------------
take_lock || exit 0
trap drop_lock EXIT
rotate_log
[ -f "$STATE/paused" ] && exit 0
if [ ! -x "$PY" ] || [ -z "$(cur_ver)" ] || [ ! -d "$HOUSE/ui/tests" ] || [ ! -s "$VERSIONS" ] || [ ! -s "$GH" ]; then
  log "not installed, or the House checkout is missing (run install_autoupdate.sh)"; exit 0
fi
date '+%F %T' > "$STATE/last_check"

rm -f "$STATE/waiting"
if vdj_running; then   # not even GitHub: no login prompt, no test CPU load, no sorter restarts
  echo "waiting while VirtualDJ is open" > "$STATE/waiting"
  exit 0
fi

if list="$(list_versions)"; then
  rm -f "$STATE/fetch_failing_since" "$STATE/notified.fetch"
else
  list=""
  [ -f "$STATE/fetch_failing_since" ] || date +%s > "$STATE/fetch_failing_since"
  if [ $(( $(date +%s) - $(cat "$STATE/fetch_failing_since") )) -gt 21600 ]; then
    notify_once fetch "AutoCue updates are stuck: GitHub has not answered for 6 hours (check the GitHub login on this Mac: gh auth login)."
  fi
  log "could not list the releases (offline or GitHub login); will retry"
fi

ver=""
[ -n "$list" ] && ver="$(pick_version "$list")"
if [ -n "$ver" ]; then
  cur="$(cur_ver)"
  kind=""; reason=""; base_dir=""
  log "testing $ver against the installed $(label "$cur")"
  rm -rf "$GATE/base" "$GATE/cand" "$GATE/work"
  if ! assets "$ver"; then
    kind=env; reason="could not download release v$ver (see $LOG)"
  elif ! missing="$("$PY" "$VERSIONS" check-deps "$(wheel_of "$ver")")"; then
    kind=deps; reason="needs $(printf '%s' "$missing" | tr '\n' ';' | sed 's/;$//') in $PY"
  elif ! gate_tree "$ver" "$GATE/cand"; then
    kind=reject; reason="its package does not install (see $LOG)"
  elif is_version "$cur" && ! { assets "$cur" && gate_tree "$cur" "$GATE/base"; }; then
    kind=env; reason="could not prepare the installed $cur for the comparison (see $LOG)"
  elif ! is_version "$cur" && ! legacy_tree "$cur" "$GATE/base"; then
    kind=env; reason="could not unpack the installed $(label "$cur") from $CHECKOUT for the comparison"
  else
    # the comparison is always the installed version's own tests around its own code
    base_dir="$GATE/base"
    result="$(with_timeout 9000 "$PY" "$APP/bin/autocue_gate.py" --python "$PY" --base "$base_dir" \
        --cand "$GATE/cand" --work "$GATE/work" --house "$HOUSE" --timeout 1200 2>>"$LOG")"
    verdict="$(printf '%s' "$result" | "$PY" -c '
import json, sys
try:
    d = json.loads(sys.stdin.read().strip().splitlines()[-1])
except Exception:
    print("reject\tthe test run stopped without a result (timeout or crash)"); sys.exit()
kind = "accept" if d.get("accept") else ("nopytest" if d.get("no_pytest") else "reject")
print(kind + "\t" + str(d.get("reason", "")).replace("\n", " "))')"
    kind="${verdict%%"$TAB"*}"; reason="${verdict#*"$TAB"}"
  fi
  log "gate: $kind: $reason"
  case "$kind" in
    accept)
      if [ -f "$STATE/paused" ]; then
        log "paused during the test; not installing"
      elif install_version "$ver"; then
        rm -f "$STATE/rejected.$ver" "$STATE/notified.rejected.$ver" "$STATE/envwait.$ver"
        log "installed $ver"
        if is_version "$cur" && "$PY" "$VERSIONS" older "$ver" "$cur"; then
          notify "AutoCue moved back to $ver (from $cur): the rule $(rule) asks for it."
        else
          notify "AutoCue $ver installed (was $(label "$cur"))."
        fi
      else
        printf '%s\n' "it passed the tests but does not install (see $LOG)" > "$STATE/rejected.$ver"
        log "could not install $ver; $(label "$cur") stays"
      fi
      ;;
    nopytest)
      notify_once nopytest "AutoCue updates need pytest: $PY -m pip install pytest"
      echo "updates wait for pytest: $PY -m pip install pytest" > "$STATE/waiting" ;;
    deps)
      notify_once "deps.$ver" "AutoCue $ver waits: it $reason. Install that, then it installs on its own."
      echo "$ver waits: it $reason" > "$STATE/waiting" ;;
    env)
      echo "$ver waits: $reason" > "$STATE/waiting"
      [ -f "$STATE/envwait.$ver" ] || : > "$STATE/envwait.$ver"
      if [ -n "$(find "$STATE/envwait.$ver" -mmin +360 2>/dev/null)" ]; then
        notify_once "env.$ver" "AutoCue $ver has been waiting for 6 hours: $reason"
      fi ;;
    *)
      printf '%s\n' "$reason" > "$STATE/rejected.$ver"
      notify_once "rejected.$ver" "AutoCue $ver was not installed: $reason" ;;
  esac
  rm -rf "$GATE/base" "$GATE/cand" "$GATE/work"
fi
model_step
updater_step
if [ -f "$STATE/paused" ]; then exit 0; fi   # paused during the check: no restarts, no cleanup
restart_step
cleanup_releases
exit 0
