import pytest
from vdj_cuer.stem_color import color_from_stems

H, M, L, N = "high", "medium", "low", "none"

@pytest.mark.parametrize("act,color", [
    ({"kick": H, "vocal": H, "instruments": M}, "yellow"),
    ({"kick": M, "vocal": M}, "yellow"),
    ({"hihat": H, "vocal": M}, "yellow"),
    ({"kick": N, "hihat": N, "vocal": H, "instruments": H}, "orange"),
    ({"kick": L, "vocal": H}, "orange"),
    ({"kick": H, "bass": H, "vocal": L}, "green"),
    ({"kick": H, "instruments": M}, "green"),
    ({"kick": H, "vocal": N, "bass": L, "instruments": L}, "purple"),
    ({"kick": N, "instruments": H, "bass": M}, "blue"),
    ({"kick": L, "instruments": H}, "blue"),
    ({"kick": L, "vocal": L, "bass": L}, "blue"),
    ({}, "blue"),
])
def test_mapping(act, color):
    assert color_from_stems(act)[0] == color

def test_quiet_flagged():
    assert color_from_stems({"kick": L})[1] == "quiet"
