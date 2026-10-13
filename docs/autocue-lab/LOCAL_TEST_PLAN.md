# Running the canonical AutoCue on the Mac

Branch `autocue-lab-alludd` of `langerkirill/vdj-autocue-lab` (private) is the one AutoCue
tree: station work of 2026-10-08 (voice-band vocals, drum/melody colors, vocal pickup rule,
loop seam re-test) + public main 834ba38 + the House safety hardening, the House cue model
(2026-10-08, 708 tracks), and the fixes since (read-back, fast saves, never replacing
markers with nothing).

## 0. One command tells you where things stand

```bash
~/src/vdj-autocue-lab/docs/autocue-lab/mac_setup.sh           # report only, changes nothing
~/src/vdj-autocue-lab/docs/autocue-lab/mac_setup.sh --apply   # update + apply the 8788 switch
```

It reports: is the checkout up to date with GitHub, does AutoCue import from it, does the
cue model load, would every save pass the database form check (read-only), is the 8788
switch in, and what 8787/8788 are running right now (and whether they need a restart).
`--apply` only fast-forwards the checkout (git refuses if your own changes overlap) and
applies the House patch when it applies cleanly. It never starts or stops servers and
never writes database.xml.

## 1. Get the code

```bash
cd ~/src && git clone -b autocue-lab-alludd https://github.com/langerkirill/vdj-autocue-lab.git
```

No new venv: the scripts use `~/src/vdj-station-house/venv` (it saved the cue model, so its
scikit-learn matches) and that repo's `.env` for the Gemini key.

## 2. Optional: trial on a copy of the database (VirtualDJ may stay open)

```bash
~/src/vdj-autocue-lab/docs/autocue-lab/trial_autocue_on_copy.sh "/path/a.mp3" "/path/b.flac"
```

Copies `database.xml` to `~/Music/DJ/AutoCue-trials/<time>/`, runs AutoCue (analysis cache
off) writing only that copy, prints each song's markers current vs new, and confirms the
real `database.xml` is byte-identical afterwards.

## 3. House sorter (8788) on the new code

Stop 8788 first and let any AutoCue / ingest / grid job finish.

```bash
~/src/vdj-autocue-lab/docs/autocue-lab/mac_setup.sh --apply          # applies the switch
VDJ_AUTOCUE_ROOT=~/src/vdj-autocue-lab ~/src/vdj-station-house/ui/start_8788_write.sh
```

The startup lines must show `autocue=/Users/kirilllanger/src/vdj-autocue-lab` and
`vdj_database_safety = /Users/kirilllanger/src/vdj-autocue-lab/vdj_database_safety.py`.
Use `start_8788.sh` instead for read-only.

**Going back:** stop 8788 and start it without `VDJ_AUTOCUE_ROOT`; the fork's own copy is
untouched. **Updates:** with automatic updates on (section 6), start 8788 with
`~/.vdj-autocue/bin/run_sorter.sh 8788` instead and it follows each tested version on its own;
without them, `mac_setup.sh --apply`, then restart 8788 (the script says when).

## 4. Main station (8787): optional

8787 keeps its own UI in `~/src/vdj-station`. This repo's `ui/` is the House UI (house
profile only), so do **not** run this repo's UI on 8787. To give 8787 the new AutoCue:

```bash
cd ~/src/vdj-station
git apply --check ~/src/vdj-autocue-lab/docs/autocue-lab/station-8787-switch.patch \
  && git apply ~/src/vdj-autocue-lab/docs/autocue-lab/station-8787-switch.patch
VDJ_AUTOCUE_ROOT=~/src/vdj-autocue-lab ui/start_8787_noreload.sh
```

Check first that `mac_setup.sh` reports the database check as OK: the new safety gates
refuse every save while any row in the file is in a shape VirtualDJ rejects, and the old
station UI used to write some. Cloud run of the station's tests on the new AutoCue: 550
passed, 4 failed (2 old, 2 harmless fixture differences). A recolor of a cue that had no
color and notes with line breaks used to be refused; the save step now puts those rows in
VirtualDJ's form. Remaining gap of the old UI: its per-song edits do not detect a
concurrent AutoCue write to the same song.

## 5. If a song lost markers

AutoCue backs up database.xml before every write. To put one song's cues and loops back
(VirtualDJ closed for `--apply`):

```bash
cd ~/src/vdj-autocue-lab
PY=~/src/vdj-station-house/venv/bin/python
$PY restore_song_markers.py --song "/full/path/song.flac" --find          # which backups have them
$PY restore_song_markers.py --song "/full/path/song.flac" --backup "<file>"   # preview
$PY restore_song_markers.py --song "/full/path/song.flac" --backup "<file>" --apply
```

## 6. Automatic updates (one-time)

AutoCue is now a package, `vdj-autocue`, with version numbers: 1.0.1 is a fix, 1.1.0 changes
the cues it writes, 2.0.0 is a breaking change (CHANGELOG.md says what each one changes). Each
version is a GitHub Release of the private repo; the Mac downloads new ones with your GitHub
login and installs them on its own after testing them there.

```bash
gh auth status || gh auth login                                   # once: the Mac's GitHub login
~/src/vdj-station-house/venv/bin/python -m pip install pytest     # once, if missing
~/src/vdj-autocue-lab/docs/autocue-lab/mac_setup.sh --apply      # adds the House busy check
~/src/vdj-autocue-lab/docs/autocue-lab/autoupdate/install_autoupdate.sh   # --uninstall to stop
~/.vdj-autocue/bin/run_sorter.sh 8788      # from now on, start 8788 this way (--readonly to look only)
```

The installer puts the newest version in place and sets the rule to `vdj-autocue>=<that
version>`: every newer version may install (patches, minors and majors; no upper bound).

How it works:

- Every 15 minutes (and at login) it looks for new releases on GitHub. It never changes the
  files of `~/src/vdj-autocue-lab`, and plain pushes without a new release install nothing.
  Downloads are checked against the release's checksums; a release that is ever changed after
  it came out is ignored.
- While VirtualDJ is open it does nothing else: no tests (they use the CPU), no restarts.
- For the newest version the rule allows, it downloads the package and its tests and tests it
  on the Mac against the installed one: AutoCue's tests and the House sorter's tests, with the
  House Python, on the packages as pip installs them. If it fails any test the installed version
  passes, or a test run crashes or times out, it is **not** installed and you get one
  notification (tried again after 6 hours, or a newer version is tried). If the version needs
  a newer library (numpy, scikit-learn, ...), it waits and the notification says what to
  install.
- A version that passes is installed by pip into its own folder
  `~/.vdj-autocue/releases/<version>`, and `~/.vdj-autocue/current` points at it. Nothing in a
  version's folder changes after that.
- `run_sorter.sh` starts 8788 on the version `current` points at, and every AutoCue job of
  that sorter uses the same version. When a newer version is installed, the sorter is
  restarted on it, but only when the sorter itself says no background job is running (AutoCue,
  grid fix, assemble, analysis, retraining, a database save; asked twice, 15 s apart), no
  AutoCue process runs, and nothing was edited in the sorter for 10 minutes. A sorter started
  any other way, or without the busy check, is never stopped; you get one notification to
  restart it instead.
- If a new version makes the sorter stop right at start 3 times, `run_sorter.sh` starts it on
  the version it last ran on; once that runs, the new version is rolled back by itself (and
  never installed again). If the old version stops too, the cause is elsewhere: nothing is
  rolled back and `run_sorter.sh` stops with a notification.
- Your retrained cue model lives in `~/.vdj-autocue/model` and is shared by every version;
  retraining always saves there. The installer copies in your newest one. A model shipped with
  a new version replaces it only if that one is newer (or the new version needs a different
  kind of model), and the old one goes to `model/backup`.

```bash
U=~/.vdj-autocue/bin/autocue_update.sh
$U --status                         # installed version, rule, newest allowed, what 8788/8787 run
$U --pin 'vdj-autocue>=1.4,<2'      # e.g. no automatic 2.0; '==1.4.2' holds one version
$U --pin 'vdj-autocue>=1.4'         # back to "every newer version"
$U --rollback                       # back to the previous version; the one you left is never installed again
$U --pause                          # stop updating (e.g. a whole gig day); --resume afterwards
```

Log: `~/Library/Logs/autocue-update.log`. 8787 can follow updates the same way
(`run_sorter.sh 8787`) once the station has `station-8787-switch.patch`; without a busy check
in the station it gets a notification instead of a restart. When an installed release comes
with a newer updater you get one notification; `$U --refresh-updater` installs it (versions,
rule and model stay).

## 7. Let the cloud session read your logs (one-time)

```bash
~/src/vdj-autocue-lab/docs/autocue-lab/log_sync/install_log_sync.sh      # --uninstall to stop
```

Every 15 minutes it pushes the sorter action logs, AutoCue job logs and the current
markers of recently edited songs to the private branch `autocue-logs` (read-only on your
library and database). Log: `~/Library/Logs/autocue-log-sync.log`.

## Still open

- Cue placement on sung-through songs (California Dreaming: cues on interrupted vocals) is
  being reworked in the cloud against your kept/deleted cues.
- Loops: no loop passed the seam test on Carousel and How Deep Is Your Love.
- Colors: "drums only" purple was wrong both times this week; two false vocal yellows on
  Carousel.
- Questions: the Gemini seam check passes loops when Gemini is unavailable; should
  approved transitions feed "Best for set"?
