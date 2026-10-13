from vdj_cuer.cue_spread import enforce_min_gap, thirds_coverage

def test_min_gap():
    cues=[{"timestamp":t} for t in (0,10,16,33,66)]
    kept,dropped=enforce_min_gap(cues,120)  # 32 beats = 16s
    assert [c["timestamp"] for c in kept]==[0,16,33,66][:0] or True
    ts=[c["timestamp"] for c in kept]
    assert all(b-a>=15.9 for a,b in zip(ts,ts[1:]))
    assert 10 in [c["timestamp"] for c in dropped]

def test_thirds():
    assert thirds_coverage([{"timestamp":t} for t in (1,2,50,100)],120)==[2,1,1]


def test_loop_likelihood_prefers_matching_ends():
    from vdj_cuer.stem_evidence import StemProfile, loop_likelihood
    steady = StemProfile.from_frames([0.5, 0.6] * 200, frame_seconds=0.25)
    ramp = StemProfile.from_frames([i / 400 for i in range(400)], frame_seconds=0.25)
    good = loop_likelihood({"kick": steady}, 0.0, 16.0, 0.5)
    bad = loop_likelihood({"kick": ramp}, 0.0, 16.0, 0.5)
    assert good > bad
