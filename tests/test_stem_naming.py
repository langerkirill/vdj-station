from vdj_cuer.cue_writer import PreparedPoi
from vdj_cuer.stem_naming import drop_cues_under_loops, name_from_stems


def P(kind, pos, color, name="x", beats=None):
    return PreparedPoi(kind=kind, name=name, position=pos, color_name=color,
                       color_value="0", elements=[], length_beats=beats)


def test_cue_under_loop_is_dropped():
    cues = [P("cue", 10.0, "green"), P("cue", 50.0, "green")]
    loops = [P("loop", 50.02, "green", beats=32)]
    kept, dropped = drop_cues_under_loops(cues, loops)
    assert [c.position for c in kept] == [10.0]
    assert [c.position for c in dropped] == [50.0]


def test_names_follow_colors_and_never_repeat():
    cues = [P("cue", 0.0, "green"), P("cue", 30.0, "blue"), P("cue", 60.0, "green"),
            P("cue", 90.0, "green"), P("cue", 120.0, "blue")]
    loops = [P("loop", 60.0, "purple"), P("loop", 90.0, "purple")]
    c, l = name_from_stems(cues, loops, color_values={}, first_one=0.0)
    names = [x.name for x in c]
    assert names[0] == "Beat Entry" and names[1] == "Breakdown"
    assert names[2] == "Drop" and names[-1] == "Outro"
    assert len(set(names)) == len(names)
    assert [x.name for x in l] == ["Drum Loop", "Beat Loop"]
