#!/bin/bash
# Check (default) or prepare (--apply) the Mac for the canonical AutoCue checkout.
#
#   ~/src/vdj-autocue-lab/docs/autocue-lab/mac_setup.sh           # report only, changes nothing
#   ~/src/vdj-autocue-lab/docs/autocue-lab/mac_setup.sh --apply   # safe steps only (see below)
#
# --apply does at most: `git pull --ff-only` of this checkout (git itself refuses when
# your local changes overlap), and `git apply` of the House switch patch(es) when they
# apply cleanly. It never starts/stops servers, never writes database.xml (it only reads
# it to check it), never moves the old AutoCue copies.
set -uo pipefail

MODE="check"
[ "${1:-}" = "--apply" ] && MODE="apply"
CANON="$(cd "$(dirname "$0")/../.." && pwd -P)"
BRANCH="main"
HOUSE="${VDJ_HOUSE_ROOT:-$HOME/src/vdj-station-house}"
STATION="${VDJ_STATION_ROOT:-$HOME/src/vdj-station}"
DB="${VDJ_DATABASE:-$HOME/Library/Application Support/VirtualDJ/database.xml}"
PATCH="$CANON/docs/autocue-lab/house-8788-switch.patch"
TEST_PATCH="$CANON/docs/autocue-lab/house-tests-verified-cache.patch"
PY="${AUTOCUE_PYTHON:-$HOUSE/venv/bin/python}"
[ -x "$PY" ] || PY="$STATION/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
ok() { echo "  OK    $*"; }
warn() { echo "  WARN  $*"; }
todo() { echo "  TODO  $*"; }
info() { echo "  --    $*"; }
inside_canon() {  # inside_canon PATH -> true if PATH is this checkout or below it
  local real
  real="$(cd "$1" 2>/dev/null && pwd -P)" || return 1
  case "$real/" in "$CANON"/*) return 0 ;; *) return 1 ;; esac
}
PULLED=0
HOUSE_SWITCH="none"

echo "== AutoCue checkout: $CANON ($MODE mode)"
cur="$(git -C "$CANON" rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ "$cur" = "$BRANCH" ]; then
  ok "on branch $BRANCH"
elif [ "$cur" = "HEAD" ]; then
  warn "not on any branch (detached); nothing will be updated. To fix: git -C $CANON switch $BRANCH"
else
  warn "on branch '$cur', not $BRANCH; nothing will be updated. To fix: git -C $CANON switch $BRANCH"
fi
err="$(GIT_TERMINAL_PROMPT=0 GIT_ASKPASS= SSH_ASKPASS= git -C "$CANON" fetch -q origin "$BRANCH" 2>&1)"
if [ $? -ne 0 ]; then
  case "$err" in
    *Authentication*|*"could not read Username"*|*"not found"*|*403*)
      warn "GitHub wants you to log in again (run: gh auth login); skipped the update check" ;;
    *) warn "could not reach GitHub (offline?); skipped the update check" ;;
  esac
elif [ "$cur" = "$BRANCH" ]; then
  behind="$(git -C "$CANON" rev-list --count "HEAD..origin/$BRANCH")"
  ahead="$(git -C "$CANON" rev-list --count "origin/$BRANCH..HEAD")"
  if [ "$behind" = "0" ]; then
    ok "up to date with GitHub ($(git -C "$CANON" rev-parse --short HEAD))"
  elif [ "$ahead" != "0" ]; then
    todo "GitHub has $behind new commit(s) and this checkout has $ahead of its own; ask Claude to merge them"
  elif [ "$MODE" = "apply" ]; then
    if git -C "$CANON" pull -q --ff-only origin "$BRANCH" 2>/dev/null; then
      ok "updated: $behind new commit(s) -> $(git -C "$CANON" rev-parse --short HEAD)"
      PULLED=1
    else
      both="$(comm -12 <(git -C "$CANON" diff --name-only HEAD | sort) <(git -C "$CANON" diff --name-only HEAD "origin/$BRANCH" | sort))"
      todo "not updated: GitHub also changed files you changed here; nothing was touched:"
      echo "$both" | sed 's/^/          /'
      echo "$both" | grep -q "ml/artifacts" && echo "        (that is your retrained cue model; ask Claude which model to keep)"
    fi
  else
    todo "GitHub has $behind new commit(s) (run with --apply to update)"
  fi
fi
changed="$(git -C "$CANON" status --porcelain --untracked-files=no 2>/dev/null)"
if [ -n "$changed" ]; then
  info "files changed in this checkout (kept as they are):"
  echo "$changed" | sed 's/^/          /' | head -15
fi

echo; echo "== Python used for AutoCue: $PY"
if [ ! -x "$PY" ]; then
  warn "no python found (set AUTOCUE_PYTHON)"
else
  ( cd "$CANON" && PYTHONPATH="$CANON" "$PY" - "$CANON" <<'PY'
import os, sys, warnings
root = os.path.realpath(sys.argv[1]) + os.sep
inside = lambda f: os.path.realpath(f).startswith(root)
def ok(m): print("  OK    " + m)
def warn(m): print("  WARN  " + m)
try:
    import vdj_database_safety as V, vdj_cuer.cue_writer as cw
    (ok if inside(V.__file__) else warn)(f"safety module from {V.__file__}")
    (ok if inside(cw.__file__) else warn)(f"AutoCue (vdj_cuer) from {os.path.dirname(cw.__file__)}")
    m = V.autocue_safety_module()
    (ok if inside(m.__file__) else warn)(f"second-opinion check: {m.__file__}")
    ok(f"fast-save cache: {V._verified_cache_file() or 'memory only'}")
except Exception as exc:
    warn(f"AutoCue does not import: {exc!r}")
for mod in ("numpy", "scipy", "sklearn", "joblib"):
    try:
        ok(f"{mod} {getattr(__import__(mod), '__version__', '?')}")
    except Exception as exc:
        warn(f"{mod} missing ({exc.__class__.__name__}); install it in this venv")
try:
    import joblib
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        joblib.load(os.path.join(root, "vdj_cuer/ml/artifacts/cue_bar_clf.joblib"))
    bad = [w for w in caught if "ersion" in str(w.message) or "Version" in type(w.message).__name__]
    if bad:
        warn("cue model loads with a scikit-learn version warning: " + str(bad[0].message)[:160])
    else:
        ok("cue model loads cleanly")
except Exception as exc:
    warn(f"cue model failed to load: {exc!r}")
PY
  )
fi

echo; echo "== Automatic updates"
APP="${AUTOCUE_HOME:-$HOME/.vdj-autocue}"
UPD="$APP/bin/autocue_update.sh"
AUTO=0
if [ -f "$HOME/Library/LaunchAgents/com.vdj-autocue.update.plist" ] && [ -x "$UPD" ] && [ -e "$APP/current" ]; then
  AUTO=1
  "$UPD" --status 2>/dev/null | sed 's/^/  /'
  stamp() { sed -n 's/^UPDATER_VERSION=\([0-9][0-9]*\).*/\1/p' "$1" 2>/dev/null | head -1 | grep . || echo 0; }
  if [ "$(stamp "$CANON/docs/autocue-lab/autoupdate/autocue_update.sh")" -gt "$(stamp "$APP/bin/autocue_update.sh")" ] 2>/dev/null; then
    todo "this checkout has a newer updater than the installed one; refresh it (keeps versions and model):"
    echo "          $CANON/docs/autocue-lab/autoupdate/install_autoupdate.sh"
  fi
elif [ -d "$HOME/Library/Caches/vdj-autocue/update" ]; then
  todo "the first (retired) updater is installed; replace it with the release-based one:"
  echo "          $CANON/docs/autocue-lab/autoupdate/install_autoupdate.sh"
else
  info "off. To get each new TESTED version automatically:"
  echo "          $CANON/docs/autocue-lab/autoupdate/install_autoupdate.sh"
fi
start_cmd() {  # start_cmd <port> <script>: how to start a sorter on the new AutoCue
  if [ "$AUTO" = 1 ]; then
    local ro=""; [ "$2" = start_8788.sh ] && ro=" --readonly"
    echo "          $APP/bin/run_sorter.sh $1$ro"
  elif [ "$1" = 8787 ]; then
    echo "          VDJ_AUTOCUE_ROOT=$CANON $CANON/ui/start_8787_noreload.sh"
  else
    echo "          VDJ_AUTOCUE_ROOT=$CANON $HOUSE/ui/$2"
  fi
}
proc_env() {  # proc_env <pid> <NAME> (macOS: -E prints the environment; -e means "all")
  local flags="eww"; [ "$(uname)" = Darwin ] && flags="-E -ww"
  # shellcheck disable=SC2086
  ps $flags -o command= -p "$1" 2>/dev/null | tr ' ' '\n' | sed -n "s/^$2=//p" | head -1
}

echo; echo "== VirtualDJ database (read-only check)"
if [ -f "$DB" ] && [ -x "$PY" ]; then
  "$PY" "$CANON/docs/autocue-lab/db_preflight.py" "$DB" | sed 's/^/  /'
else
  info "no database at $DB"
fi

echo; echo "== House sorter (8788): $HOUSE"
if ! git -C "$HOUSE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  warn "no git repo at $HOUSE (set VDJ_HOUSE_ROOT)"
else
  missing=""
  for f in ui/sorter/autocue_path.py ui/start_8788.sh ui/start_8788_write.sh; do
    grep -q VDJ_AUTOCUE_ROOT "$HOUSE/$f" 2>/dev/null || missing="$missing $f"
  done
  grep -q 'repo_root = ensure_autocue_on_path()' "$HOUSE/ui/sorter/grid_preflight.py" 2>/dev/null || missing="$missing ui/sorter/grid_preflight.py"
  if [ -z "$missing" ]; then
    HOUSE_SWITCH="complete"
    ok "switch is in (all four files)"
  elif [ "$missing" != " ui/sorter/autocue_path.py ui/start_8788.sh ui/start_8788_write.sh ui/sorter/grid_preflight.py" ]; then
    HOUSE_SWITCH="partial"
    warn "switch only partly in; still missing:$missing (ask Claude to finish it)"
  elif git -C "$HOUSE" apply --check "$PATCH" 2>/dev/null; then
    if [ "$MODE" = "apply" ]; then
      git -C "$HOUSE" apply "$PATCH" && ok "applied the 8788 switch" && HOUSE_SWITCH="complete"
    else
      todo "the 8788 switch is not in yet; it applies cleanly (run with --apply)"
    fi
  else
    warn "the 8788 switch is not in and does not apply cleanly; ask Claude to merge it"
  fi
  RACE_PATCH="$CANON/docs/autocue-lab/house-remove-song-race.patch"
  if grep -q "remove_song_xml_in_database" "$HOUSE/ui/sorter/relocate.py" 2>/dev/null; then
    ok "House 'remove song' uses the locked database helper"
  elif [ -f "$RACE_PATCH" ] && git -C "$HOUSE" apply --check "$RACE_PATCH" 2>/dev/null; then
    if [ "$MODE" = "apply" ]; then
      git -C "$HOUSE" apply "$RACE_PATCH" && ok "applied the House 'remove song' fix (restart 8788 to load it)"
    else
      todo "House 'remove song' fix applies cleanly (run with --apply)"
    fi
  else
    warn "House 'remove song' fix does not apply cleanly; ask Claude to merge it"
  fi
  BUSY_PATCH="$CANON/docs/autocue-lab/house-busy-endpoint.patch"
  if [ -f "$HOUSE/ui/sorter/busy.py" ] && grep -q '"/api/busy"' "$HOUSE/ui/app.py" 2>/dev/null; then
    ok "House sorter reports when it is busy (/api/busy; automatic restarts on new AutoCue versions)"
  elif [ -f "$BUSY_PATCH" ] && git -C "$HOUSE" apply --check "$BUSY_PATCH" 2>/dev/null; then
    if [ "$MODE" = "apply" ]; then
      git -C "$HOUSE" apply "$BUSY_PATCH" && ok "applied the House busy check (restart 8788 to load it)"
    else
      todo "House busy check (/api/busy, for automatic restarts) applies cleanly (run with --apply)"
    fi
  else
    warn "House busy check does not apply cleanly; 8788 will not restart on its own (ask Claude)"
  fi
  if grep -q "VDJ_DB_VERIFIED_CACHE" "$HOUSE/ui/tests/conftest.py" 2>/dev/null; then
    ok "House tests keep off the real fast-save cache"
  elif [ ! -f "$TEST_PATCH" ]; then
    warn "missing $TEST_PATCH"
  elif git -C "$HOUSE" apply --check "$TEST_PATCH" 2>/dev/null; then
    if [ "$MODE" = "apply" ]; then
      git -C "$HOUSE" apply "$TEST_PATCH" && ok "applied the House tests patch"
    else
      todo "House tests patch applies cleanly (run with --apply)"
    fi
  else
    warn "House tests patch does not apply; House tests would use the real fast-save cache (ask Claude)"
  fi
fi

echo; echo "== Running servers"
if ! command -v lsof >/dev/null 2>&1; then
  warn "lsof not found; cannot check running servers"
else
  if [ "$(uname)" = "Darwin" ]; then
    code_changed="$(stat -f %m "$CANON/.git/logs/HEAD" 2>/dev/null || echo 0)"
  else
    code_changed="$(stat -c %Y "$CANON/.git/logs/HEAD" 2>/dev/null || echo 0)"
  fi
  for port in 8788 8787; do
    pid="$(lsof -nP -iTCP:$port -sTCP:LISTEN -t 2>/dev/null | head -1)"
    if [ -z "$pid" ]; then
      info "nothing running on $port"
      continue
    fi
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
    envroot="$(proc_env "$pid" VDJ_AUTOCUE_ROOT)"
    ro="$(proc_env "$pid" MUSIC_SORTER_READONLY)"
    started="$("$PY" -c 'import sys,time
e=sys.argv[1].strip(); d,_,r=e.rpartition("-"); p=[int(x) for x in r.split(":")]
s=(int(d) if d else 0)*86400+sum(v*60**i for i,v in enumerate(reversed(p)))
print(int(time.time())-s)' "$(ps -o etime= -p "$pid" 2>/dev/null)" 2>/dev/null || echo 0)"
    mode_word="read-only"; script="start_8788.sh"
    [ "$ro" = "0" ] && { mode_word="write mode"; script="start_8788_write.sh"; }
    stop="Stop it (Ctrl+C in its Terminal window, or: kill $pid), then start it with:"
    [ "$port" = 8787 ] && script="start_8787_noreload.sh"
    app_real="$(cd "$APP" 2>/dev/null && pwd -P)"
    if [ -n "$envroot" ] && [ -n "$app_real" ] && case "$envroot" in "$APP"/*|"$app_real"/*) true ;; *) false ;; esac; then
      rec="$(cut -f1 "$APP/state/run_sorter.$port" 2>/dev/null)"
      if [ "$(cd "$envroot" 2>/dev/null && pwd -P)" = "$(cd "$APP/current" 2>/dev/null && pwd -P)" ] \
          && [ "$envroot" != "$APP/current" ]; then
        ok "$port runs the installed AutoCue $(basename "$envroot" | cut -c1-7) ($mode_word)"
      elif [ "$envroot" = "$APP/current" ] || [ "$envroot" = "$app_real/current" ]; then
        todo "$port was started on the 'current' link itself (a version switch would change code under it). $stop"
        start_cmd "$port" "$script"
      elif [ "$rec" = "$pid" ]; then
        info "$port runs an earlier AutoCue; run_sorter.sh restarts it on the new one once idle"
      else
        todo "$port runs an earlier AutoCue. $stop"
        start_cmd "$port" "$script"
      fi
    elif [ -n "$envroot" ]; then
      if ! inside_canon "$envroot" || [ "$(cd "$envroot" && pwd -P)" != "$CANON" ]; then
        warn "$port uses AutoCue from $envroot, NOT from $CANON. $stop"
        start_cmd "$port" "$script"
      elif [ "$port" = 8788 ] && [ "$HOUSE_SWITCH" != "complete" ]; then
        warn "$port has VDJ_AUTOCUE_ROOT set, but the House code ignores it until the switch is in (see above)"
      elif [ "$AUTO" = 1 ]; then
        todo "$port runs AutoCue from the checkout, not the auto-updated version. $stop"
        start_cmd "$port" "$script"
      elif [ "${code_changed:-0}" -gt "${started:-0}" ] 2>/dev/null; then
        todo "$port runs the new AutoCue but started before the latest update; restart it to load it. $stop"
        start_cmd "$port" "$script"
      else
        ok "$port runs the new AutoCue from this checkout ($mode_word)"
      fi
    elif [ -n "$cwd" ] && inside_canon "$cwd"; then
      ok "$port runs from this checkout ($mode_word)"
    elif [ "$port" = 8788 ]; then
      todo "$port runs the House's own old AutoCue copy ($mode_word). $stop"
      start_cmd "$port" "$script"
    else
      info "$port (main station) uses its own AutoCue copy; switching it is optional (LOCAL_TEST_PLAN.md, 8787)"
    fi
  done
fi
if [ "$PULLED" = 1 ]; then
  echo
  if [ "$AUTO" = 1 ]; then
    echo "Updated this checkout from GitHub (the sorters run the auto-updated versions, not this checkout)."
  else
    echo "Updated from GitHub: restart 8788 (as shown above) to load the new code."
  fi
fi
echo
echo "Done ($MODE mode). Nothing was started or stopped; database.xml was only read."
