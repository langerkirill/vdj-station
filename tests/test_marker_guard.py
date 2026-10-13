"""R-88 writer guard (TL calls 2026-10-12): Kirill's markers are kept exactly as they are."""
import json

from vdj_cuer.cue_writer import PreparedPoi
from vdj_cuer.deleted_markers import filter_deleted
from vdj_cuer.marker_guard import guard_plan, last_written, save_written

Y, G, B = "4294967040", "4278255360", "4278190335"
BEAT = 60 / 128.0


def cue(pos, name="Drop", color=Y, el=("drums",)):
    return PreparedPoi("cue", name, pos, "yellow", color, list(el))


def loop(pos, beats=16, name="Groove Loop", color=G):
    return PreparedPoi("loop", name, pos, "green", color, ["drums"], length_beats=beats)


def rec(*ms, tmp_path, song="/m/a.mp3"):
    store = tmp_path / "w.json"
    save_written(song, ms, job_id="j1", store=store)
    return last_written(song, store=store, jobs_file=tmp_path / "none.json", backup_dir=tmp_path)


def _keeps(res, m):
    pool = res.cues if m.kind == "cue" else res.loops
    return any((p.position, p.name, p.color_value, p.length_beats) == (m.position, m.name, m.color_value, m.length_beats)
               for p in pool)


def test_hand_moved_cue_is_kept_and_the_new_one_collides(tmp_path):
    w = rec(cue(60.0), tmp_path=tmp_path)
    moved = cue(60.4)  # Kirill nudged it (same bar, > 0.25 s)
    res = guard_plan([cue(60.0)], [], [moved], [], w, beat=BEAT)
    assert _keeps(res, moved) and moved in res.kept_hand
    assert len(res.cues) == 1 and res.dropped_new  # same bar -> new proposal dropped


def test_hand_rename_and_recolor_are_kept(tmp_path):
    w = rec(cue(30.0), cue(90.0), tmp_path=tmp_path)
    renamed, recolored = cue(30.0, name="Lovely Piano"), cue(90.0, color=B)
    res = guard_plan([cue(30.0), cue(90.0)], [], [renamed, recolored], [], w, beat=BEAT)
    assert _keeps(res, renamed) and _keeps(res, recolored)
    assert [c.name for c in res.cues] == ["Lovely Piano", "Drop"]


def test_hand_loop_including_8_beat_is_kept_and_overlap_dropped(tmp_path):
    w = rec(loop(40.0), tmp_path=tmp_path)
    hand8 = loop(40.0, beats=8)  # Kirill shortened AutoCue's loop to 8 beats
    res = guard_plan([], [loop(41.875), loop(120.0)], [], [hand8], w, beat=BEAT)
    assert _keeps(res, hand8)
    assert [l.position for l in res.loops] == [40.0, 120.0]


def test_unknown_history_keeps_everything(tmp_path):
    res = guard_plan([cue(100.0)], [], [cue(10.0), cue(50.0)], [], None, beat=BEAT)
    assert len(res.kept_hand) == 2 and len(res.cues) == 3


def test_autocue_replaces_its_own_untouched_marker(tmp_path):
    w = rec(cue(60.0), loop(80.0), tmp_path=tmp_path)
    res = guard_plan([cue(70.0, name="Break")], [loop(150.0)], [cue(60.0)], [loop(80.0)], w, beat=BEAT)
    assert [c.position for c in res.cues] == [70.0] and [l.position for l in res.loops] == [150.0]
    assert len(res.replaced_own) == 2 and not res.kept_hand


def test_own_markers_stay_when_that_side_has_nothing_new(tmp_path):
    w = rec(loop(80.0), tmp_path=tmp_path)
    res = guard_plan([cue(10.0)], [], [], [loop(80.0)], w, beat=BEAT)
    assert [l.position for l in res.loops] == [80.0]


def test_deleted_marker_is_never_re_added(tmp_path, monkeypatch):
    store = tmp_path / "deleted.json"
    store.write_text(json.dumps({"markers": [
        {"path": "/m/a.mp3", "kind": "cue", "pos": 45.94, "name": "Drop", "size": None}]}))
    monkeypatch.setenv("AUTOCUE_DELETED_MARKERS_PATH", str(store))
    cues, _, msgs = filter_deleted([cue(45.94)], [], "/m/a.mp3")
    res = guard_plan(cues, [], [], [], None, beat=BEAT)
    assert not any(abs(c.position - 45.94) < 0.3 for c in res.cues)


def test_besomage_kirills_chorus_wins(tmp_path):
    # His Chorus at 30.94 (AutoCue had written 'Beat Entry' there, he deleted + re-made it).
    w = rec(cue(30.944, name="Beat Entry"), tmp_path=tmp_path)
    chorus = cue(30.944, name="Chorus")
    res = guard_plan([cue(30.944, name="Beat Entry"), cue(31.5, name="Vocal Entry")], [], [chorus], [], w,
                     beat=BEAT, offset=0.944)
    assert _keeps(res, chorus)
    assert [c.name for c in res.cues] == ["Chorus"]


def test_besomage_new_cue_never_repeats_kirills_chorus(tmp_path):
    # Oct 12 dry-run: Kirill's kept 'Chorus' @ 30.944 and a new stem-named 'Chorus' @ 60.945.
    w = rec(cue(30.944, name="Beat Entry"), cue(60.945, name="Vocal Groove"), tmp_path=tmp_path)
    chorus = cue(30.944, name="Chorus")
    own = cue(60.945, name="Vocal Groove")
    res = guard_plan([cue(60.945, name="Chorus")], [], [chorus, own], [], w, beat=BEAT, offset=0.944)
    assert _keeps(res, chorus)
    names = [c.name for c in res.cues]
    assert names.count("Chorus") == 1
    assert names == ["Chorus", "Vocal Groove"]


def test_matching_uses_position_name_and_type_never_the_hotcue_slot(tmp_path):
    # AutoCue renumbers jump cues in time order, so the slot (Num) of an untouched marker can
    # change; a different marker can also land in the slot AutoCue used. Neither counts.
    w = rec(cue(30.944, name="Beat Entry"), cue(60.945, name="Groove", color=G), tmp_path=tmp_path)
    own = dict(kind="cue", position=60.945, name="Groove", color=G, num="1")      # slot moved 2 -> 1
    hand = dict(kind="cue", position=45.0, name="Beat Entry", color=Y, num="1")    # his, in AutoCue's old slot
    from vdj_cuer.marker_guard import split_existing

    hand_out, own_out = split_existing([own, hand], w["markers"])
    assert own_out == [own]
    assert hand_out == [hand]
    loop_as_cue = dict(kind="cue", position=60.945, name="Groove", color=G, num="2")
    assert split_existing([dict(loop_as_cue, kind="loop", length_beats=16)], w["markers"])[1] == []

