"""Voice-band vocal detection (Kirill 2026-10-08: "more accuracy identifying vocals").

The 800 Hz peak envelope keeps only < 400 Hz, where most of a voice is missing, so
real vocal songs (Weiss - Feel My Needs, Clementine Douglas/MK - Asking) were judged
as bleed. Vocal decisions now use a 250-4000 Hz envelope of the vocal and instruments
stems when it is available. Thresholds come from 101 hand-recolored markers on 40
songs (leave-one-song-out accuracy 0.95, recall 0.91 vs 0.81 / 0.62 before).
"""
import array
import dataclasses
import math
import os
import tempfile
import unittest
import wave

from vdj_cuer.stem_evidence import (
    StemProfile,
    load_stem_profiles,
    measure_stem_evidence,
    vocal_stem_is_usable,
)


def _band(profile, voice, seconds=1.0):
    return dataclasses.replace(
        profile, voice_frames=tuple(voice), voice_frame_seconds=seconds,
        voice_reference=sorted(voice)[round((len(voice) - 1) * 0.95)],
    )


def _profiles(vocal_band, inst_band, modulation=1.4, low_vocal=0.02, low_inst=0.5):
    n = len(vocal_band)
    vocal = StemProfile.from_frames([low_vocal] * n, 1.0)
    vocal = dataclasses.replace(_band(vocal, vocal_band), voice_modulation=modulation)
    inst = _band(StemProfile.from_frames([low_inst] * n, 1.0), inst_band)
    return {"vocal": vocal, "instruments": inst,
            "kick": StemProfile.from_frames([0.8] * n, 1.0)}


def _phrase(n=100, start=30, end=70, level=40.0, floor=0.5):
    return [level if start <= i < end else floor for i in range(n)]


class VoiceBandGateTests(unittest.TestCase):
    def test_weiss_like_song_is_vocal_although_low_band_ratio_is_under_the_old_floor(self):
        # Low-band vocal/instruments 0.04 (old rule: bleed); voice band 0.58, modulated.
        profiles = _profiles(_phrase(level=45.0), [77.0] * 100, modulation=1.45)
        self.assertTrue(vocal_stem_is_usable(profiles))
        ev = measure_stem_evidence(profiles, 40.0, 8.0, [], strict_drums=False, vocal_lookahead=4.0)
        self.assertIn("vocals", ev.elements)

    def test_bliss_like_steady_synth_bleed_is_not_vocal(self):
        profiles = _profiles(_phrase(level=41.0), [76.0] * 100, modulation=0.72)
        self.assertFalse(vocal_stem_is_usable(profiles))

    def test_quiet_bleed_in_the_voice_band_is_not_vocal(self):
        # Malibu-like: modulated but only 9% of the instruments in the voice band.
        profiles = _profiles(_phrase(level=6.4), [73.0] * 100, modulation=1.57)
        self.assertFalse(vocal_stem_is_usable(profiles))

    def test_silent_vocal_stem_is_not_vocal_even_with_silent_instruments(self):
        profiles = _profiles([0.2] * 100, [0.1] * 100)
        self.assertFalse(vocal_stem_is_usable(profiles))

    def test_unmeasured_modulation_does_not_block(self):
        profiles = _profiles(_phrase(level=60.0), [60.0] * 100, modulation=None)
        self.assertTrue(vocal_stem_is_usable(profiles))

    def test_without_voice_band_the_old_rule_still_applies(self):
        vocal = StemProfile.from_frames([0.3] * 100, 1.0)
        inst = StemProfile.from_frames([0.5] * 100, 1.0)
        self.assertTrue(vocal_stem_is_usable({"vocal": vocal, "instruments": inst}))
        quiet = StemProfile.from_frames([0.05] * 100, 1.0)
        self.assertFalse(vocal_stem_is_usable({"vocal": quiet, "instruments": inst}))


class VoiceBandWindowTests(unittest.TestCase):
    def test_short_sung_hook_inside_the_section_counts(self):
        # A 7 s hook (22% of a 32 s section) at a fifth of the song's vocal p95.
        band = [0.5] * 200
        for i in range(100, 160):
            band[i] = 50.0
        for i in range(30, 37):
            band[i] = 11.0
        profiles = _profiles(band, [60.0] * 200)
        ev = measure_stem_evidence(profiles, 29.0, 8.0, [], strict_drums=False, vocal_lookahead=4.0)
        self.assertIn("vocals", ev.elements)

    def test_a_stray_one_second_chop_does_not_count(self):
        band = [0.5] * 200
        for i in range(100, 160):
            band[i] = 50.0
        band[40] = 50.0
        profiles = _profiles(band, [60.0] * 200)
        ev = measure_stem_evidence(profiles, 29.0, 8.0, [], strict_drums=False, vocal_lookahead=4.0)
        self.assertNotIn("vocals", ev.elements)


def _write_tone(path, freq, seconds=6.0, rate=16000, amp=0.4):
    samples = array.array("h", (int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate))
                                for i in range(int(seconds * rate))))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


class VoiceBandDecodeTests(unittest.TestCase):
    def test_decode_measures_the_voice_band_not_just_the_low_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            voice = os.path.join(tmp, "voice.wav")
            low = os.path.join(tmp, "low.wav")
            _write_tone(voice, 1000.0)
            _write_tone(low, 100.0)
            v = StemProfile.decode(voice, band=True)
            lo = StemProfile.decode(low, band=True)
        self.assertIsNotNone(v.voice_frames)
        # The 800 Hz envelope barely sees a 1 kHz voice; the voice band does.
        self.assertLess(v.reference_peak, 0.1)
        self.assertGreater(v.voice_reference, 20 * lo.voice_reference)

    def test_load_stem_profiles_measures_voice_band_for_vocal_and_instruments_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for name in ("vocal", "instruments", "kick"):
                paths[name] = os.path.join(tmp, f"{name}.wav")
                _write_tone(paths[name], 800.0, seconds=3.0)
            profiles = load_stem_profiles(list(paths.items()))
        self.assertIsNotNone(profiles["vocal"].voice_frames)
        self.assertIsNotNone(profiles["instruments"].voice_frames)
        self.assertIsNone(profiles["kick"].voice_frames)


if __name__ == "__main__":
    unittest.main()
