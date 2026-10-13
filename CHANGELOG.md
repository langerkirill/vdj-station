# vdj-autocue changelog

AutoCue writes cue points, loops and colors into VirtualDJ's `database.xml`. Versions follow
semantic versioning (docs/autocue-lab/RELEASING.md):

- **PATCH** (1.0.x): fixes, safety and speed, with no intended change to the cues written.
- **MINOR** (1.x.0): changes to what AutoCue writes (cue placement, loops, colors, vocals) or
  new features.
- **MAJOR** (x.0.0): breaking changes (database write rules, model features that need a
  retrain, the job interface the sorters use).

Each version is the git tag `v<version>` and its GitHub Release. The Mac installs the newest version its rule allows
(`vdj-autocue>=<installed>`, no upper bound) after it passes the tests there.

## 1.2.0 (2026-10-12)

Built from `vdj-station` (the `vdj-autocue-lab` repo is retired). Fixes from Kirill's Oct 10
House batch (Regression Log R-10, R-88, R-89, R-92).

Your markers
- AutoCue never moves, renames, recolors, resizes or removes a cue or loop you made or
  edited (R-88). It only adds markers, or replaces its own markers nobody touched since its
  last write. Markers are matched by type, position, name and color (loops also by length),
  never by hotcue slot, so renumbered slots don't make your markers look like AutoCue's.
  `AUTOCUE_MARKER_GUARD=0` turns the guard off.
- A new cue never repeats the name of a cue already on the song (Besomage: your "Chorus" at
  0:30.9 plus a second "Chorus"); the repeat gets another name for its color.

Colors
- A cue or loop is yellow only when the vocal really opens with the drums; a medium vocal
  in the background no longer makes it yellow (R-10).

Loops
- The kick-timing check on loop seams measures the right window, so loops on a correct grid
  pass again (R-89). When fewer than 2 loops pass, AutoCue tries the other phrase-aligned
  spots before giving up. The loop checks are not loosened: fewer than 2 loops is allowed,
  with a log message saying why.

Names
- "Outro" only for the song's genuine ending part; a drumless section in the middle of the
  song is named after what plays (for example Piano Break) (R-92).

## 1.0.0 (2026-10-09)

First packaged release: the canonical AutoCue for the House sorter (8788) and the main
station (8787).

Cues and loops
- Cues on vocals that are sung through the press point are moved to a quiet bar within 2
  bars, earlier or later (voice-band test on the vocal stem), or skipped; a line entering on
  the downbeat is a clean entry. California Dreaming: 6/6 bad cues caught.
- A moved cue never lands within 32 beats of another cue (R-08).
- Loops fit the space before the next cue; the stem-envelope seam check no longer vetoes
  loops (DSP loudness limit 6 dB), so loops Kirill keeps pass again.
- When a run plans no new cues (or no new loops), the existing ones are kept instead of
  being wiped.
- The analysis cache is tied to the analysis code, so new rules apply on re-runs.

Database safety
- Read-back after a write compares VirtualDJ's canonical row form (no false "not saved").
- Healing covers multi-line comments, the footer indent and VirtualDJ's Poi attribute order.
- Insert, clone, relocate and remove re-read the database inside the lock (no lost updates
  between concurrent writers).
- Fast save path for large databases, with full checks whenever anything is uncertain.
- The second-opinion safety module defaults to this one (no hard-coded path).

Model and tools
- One shared cue model folder across versions (`VDJ_AUTOCUE_MODEL_DIR`); retrains always
  save there, atomically; an unusable shared model falls back to the shipped one.
- `restore_song_markers.py` puts one song's cues and loops back from a backup.
- Training labels read the Music Sorter UI named by `VDJ_SORTER_UI_DIR` (the sorter that
  runs AutoCue), so an installed package needs no UI copy.
