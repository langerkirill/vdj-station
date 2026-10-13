"""Band-based drum / melody flags for marker colors (Kirill 2026-10-08).

Kirill's hand recolors: 11 blue and 10 purple markers came out green. Hats alone counted as
drums (Insi, Decompression, Bliss breakdowns) and the bass stem counted as melody (Blue
Shadow, Papercut, Connected, Malibu). Now, when band envelopes exist:
  drums  = first-bar full-band KICK energy >= 15% of the kick stem's own p95 (hats alone: no)
  melody = 8-beat INSTRUMENTS voice-band energy >= 22% of its own p95 (bass is not melody)
Leave-one-song-out exact color on 91 hand-colored markers: 0.66 -> 0.82.
"""
import array
import dataclasses
import math
import os
import tempfile
import unittest
import wave

from vdj_cuer.stem_color import classify_onset, color_from_stems
from vdj_cuer.stem_evidence import (
    StemProfile,
    load_stem_profiles,
    section_band_flags,
)


def _prof(energy=None, voice=None, n=64):
    p = StemProfile.from_frames([0.5] * n, 0.5)
    if energy is not None:
        p = dataclasses.replace(p, energy_frames=tuple(energy), voice_frame_seconds=0.5,
                                energy_reference=sorted(energy)[round((len(energy) - 1) * 0.95)])
    if voice is not None:
        p = dataclasses.replace(p, voice_frames=tuple(voice), voice_frame_seconds=0.5,
                                voice_reference=sorted(voice)[round((len(voice) - 1) * 0.95)])
    return p


def _profiles(kick, inst):
    return {"kick": _prof(energy=kick), "instruments": _prof(voice=inst),
            "hihat": StemProfile.from_frames([0.9] * len(kick), 0.5),
            "bass": StemProfile.from_frames([0.9] * len(kick), 0.5)}


LOUD, QUIET = 10.0, 0.2


class SectionBandFlagsTests(unittest.TestCase):
    def test_no_band_data_means_no_opinion(self):
        p = {"kick": StemProfile.from_frames([0.5] * 64, 0.5),
             "instruments": StemProfile.from_frames([0.5] * 64, 0.5)}
        self.assertIsNone(section_band_flags(p, 10.0, 2.0, 4.0))

    def test_hats_without_kick_are_not_drums(self):
        kick = [LOUD] * 20 + [QUIET] * 44   # kick stops at 10 s; hat stem stays loud
        flags = section_band_flags(_profiles(kick, [LOUD] * 64), 12.0, 2.0, 4.0)
        self.assertFalse(flags["drums"])
        self.assertTrue(flags["melody"])

    def test_bass_alone_is_not_melody(self):
        # Instruments play elsewhere in the song but not in this section; bass stem is loud.
        inst = [QUIET] * 32 + [LOUD] * 32
        flags = section_band_flags(_profiles([LOUD] * 64, inst), 4.0, 2.0, 4.0)
        self.assertTrue(flags["drums"])
        self.assertFalse(flags["melody"])

    def test_silent_stems_never_count(self):
        flags = section_band_flags(_profiles([QUIET] * 64, [QUIET] * 64), 12.0, 2.0, 4.0)
        self.assertFalse(flags["drums"])
        self.assertFalse(flags["melody"])


class ColorWithBandFlagsTests(unittest.TestCase):
    ACT = {"kick": "high", "hihat": "high", "bass": "high", "instruments": "medium", "vocal": "none"}
    SC = {"kick": 0.9, "hihat": 0.9}

    def test_drums_plus_bass_only_is_purple(self):
        flags = {"drums": True, "melody": False}
        self.assertEqual(classify_onset(self.ACT, self.SC, None, self.SC, band_flags=flags)["color"], "purple")
        self.assertEqual(color_from_stems(self.ACT, band_flags=flags)[0], "purple")

    def test_hat_only_opening_is_blue_and_never_a_drop(self):
        out = classify_onset(self.ACT, self.SC, None, self.SC, band_flags={"drums": False, "melody": True})
        self.assertEqual(out["color"], "blue")
        self.assertFalse(out["drop_ok"])

    def test_vocals_still_win_with_band_flags(self):
        act = {**self.ACT, "vocal": "medium"}
        self.assertEqual(color_from_stems(act, band_flags={"drums": True, "melody": False})[0], "yellow")
        self.assertEqual(color_from_stems(act, band_flags={"drums": False, "melody": True})[0], "orange")

    def test_without_band_flags_behavior_is_unchanged(self):
        self.assertEqual(color_from_stems(self.ACT)[0], "green")


def _tone(path, freq, seconds=4.0, rate=16000):
    s = array.array("h", (int(0.4 * 32767 * math.sin(2 * math.pi * freq * i / rate))
                          for i in range(int(seconds * rate))))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(s.tobytes())


class DecodeEnergyTests(unittest.TestCase):
    def test_kick_gets_a_full_band_energy_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name in ("kick", "instruments", "bass"):
                p = os.path.join(tmp, name + ".wav")
                _tone(p, 60.0 if name != "instruments" else 900.0)
                paths.append((name, p))
            prof = load_stem_profiles(paths)
        self.assertIsNotNone(prof["kick"].energy_frames)
        self.assertGreater(prof["kick"].energy_reference, 1.0)
        self.assertIsNone(prof["bass"].energy_frames)


if __name__ == "__main__":
    unittest.main()
