"""R-10 retune: Kirill's kept yellows / hand recolors (voice-band shares measured 2026-10-12)."""
from types import SimpleNamespace

import pytest

from vdj_cuer.stem_color import gate_opening_vocal
from vdj_cuer.stem_evidence import POST_BOUNDARY_OFFSET_SECONDS, VOICE_FINE_HOP_SECONDS

BEAT = 0.5


def profile(s40, s20):
    """8 beats of 20 ms frames: s40 share at 0.5*ref, (s20-s40) at 0.3*ref, rest silent."""
    n = int(8 * BEAT / VOICE_FINE_HOP_SECONDS) + 2
    a, b = round(s40 * n), round(s20 * n)
    frames = [0.5] * a + [0.3] * (b - a) + [0.0] * (n - b)
    pad = int(POST_BOUNDARY_OFFSET_SECONDS / VOICE_FINE_HOP_SECONDS) + 1
    return SimpleNamespace(voice_fine_frames=[0.0] * pad + frames + [0.0] * 50, voice_fine_reference=1.0)


CASES = [  # (label, kind, s40, s20, stays_vocal)
    ("BUNT. i need u Drop @75.02", "cue", 0.04, 0.12, True),
    ("060 Chorus @101.47", "cue", 0.41, 0.82, True),
    ("060 Vocal Groove @130.73", "cue", 0.07, 0.56, True),
    ("060 @193.17", "cue", 0.37, 0.55, True),
    ("058 Vocal Groove Loop @7.87", "loop", 0.83, 1.00, True),
    ("060 loop @343.41", "loop", 0.79, 0.99, True),
    ("059 cue @39.96 (Kirill -> green)", "cue", 0.00, 0.00, False),
    ("058 Chorus Loop @31.48 (Kirill -> green)", "loop", 0.31, 0.50, False),
    ("278 5 Lan loop @45.91 (kept yellow)", "loop", 0.33, 0.51, True),
    ("040 June Freedom Chorus Vocal Loop @62.63 (kept yellow)", "loop", 0.349, 0.60, True),
    ("453 Aaliyah Chorus Loop @31.66 (kept yellow)", "loop", 0.344, 0.58, True),
    ("060 loop @421.46 (Kirill -> purple)", "loop", 0.01, 0.24, False),
    ("Dreams Synth Intro @0.30", "cue", 0.00, 0.00, False),
    ("Dreams Breakdown @190.71", "cue", 0.00, 0.01, False),
]


@pytest.mark.parametrize("label,kind,s40,s20,vocal", CASES, ids=[c[0] for c in CASES])
def test_answer_key(label, kind, s40, s20, vocal):
    act, _ = gate_opening_vocal({"vocal": "medium", "drums": "high"}, profile(s40, s20), 0.0, BEAT, kind=kind)
    assert (act["vocal"] != "low") == vocal, label


# Kirill's kept yellows that NO opening-vocal threshold can keep without also re-yellowing his
# 059 @39.96 green fix (0.00 sung in 8 beats, 0.18 in 16, 0.45 in 32): the vocal only enters
# bars later (Kylie: first sung frame at 22.24 s, 6.8 s after the cue). The writer guard keeps
# his markers as they are, so these only affect a NEW AutoCue proposal at the same spot.
@pytest.mark.xfail(strict=True, reason="vocal enters after the 2-bar opening; conflicts with 059 @39.96")
@pytest.mark.parametrize("label,s40,s20", [("Kylie Love at First Sight Verse 1 @15.47", 0.0, 0.0),
                                         ("Nicolas Giordano Aerial Synth Groove @288.00", 0.0, 0.0)])
def test_late_vocal_kept_yellows(label, s40, s20):
    act, _ = gate_opening_vocal({"vocal": "medium"}, profile(s40, s20), 0.0, BEAT, kind="cue")
    assert act["vocal"] != "low", label
