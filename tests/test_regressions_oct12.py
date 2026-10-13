"""Oct 12 2026 regressions from Kirill's Oct 10 fest16 batch: R-10, R-88, R-89, R-92."""
import unittest
from dataclasses import dataclass, field
from typing import List

import numpy as np

from vdj_cuer import cue_hygiene as H
from vdj_cuer import loop_seam_dsp as L
from vdj_cuer.stem_color import color_from_stems, gate_opening_vocal, vocal_opening_share
from vdj_cuer.stem_naming import is_song_ending, name_from_stems

HOP = 0.02


class FakeVocal:
    """Vocal stem with a coarse 0.25 s envelope and the 20 ms voice band."""

    def __init__(self, fine: List[float], ref: float = 1.0):
        self.voice_fine_frames = tuple(fine)
        self.voice_fine_reference = ref
        self.frame_seconds = 0.25
        n = int(len(fine) * HOP / 0.25)
        self.frames = tuple(
            sum(fine[int(i * 0.25 / HOP): int((i + 1) * 0.25 / HOP)]) / (0.25 / HOP) for i in range(n)
        )
        self.reference_peak = ref

    def _window(self, start, dur):
        a = int(max(0.0, start) / self.frame_seconds)
        return self.frames[a: max(a + 1, int((start + dur) / self.frame_seconds))]


def envelope(length_s: float, segments):
    """segments: [(start_s, end_s, level)] -> 20 ms frames."""
    out = [0.0] * int(length_s / HOP)
    for a, b, v in segments:
        for i in range(int(a / HOP), min(len(out), int(b / HOP))):
            out[i] = v
    return out


# --------------------------------------------------------------------- R-10
class R10OpeningVocal(unittest.TestCase):
    beat = 60 / 124

    def test_059_cue_39_96_vocal_only_later_is_not_yellow(self):
        # 059 @39.96: no singing in the first 2 bars (measured 0.00), the line comes in bars later.
        v = FakeVocal(envelope(120, [(39.96 + 8 * self.beat + 1, 80, 0.9)]))
        act = {"vocal": "medium", "kick": "high", "hihat": "medium", "bass": "high", "instruments": "medium"}
        gated, why = gate_opening_vocal(act, v, 39.96, self.beat, kind="cue")
        self.assertEqual(gated["vocal"], "low")
        self.assertTrue(why)
        self.assertEqual(color_from_stems(gated)[0], "green")

    def test_060_cue_with_sung_opening_stays_yellow(self):
        # 060 @130.73 (Kirill kept yellow): ~7% sung frames in the first 2 bars.
        segs = [(130.73 + 0.5, 130.73 + 0.5 + 0.08 * 8 * self.beat * 1.2, 0.9)]
        v = FakeVocal(envelope(200, segs))
        act = {"vocal": "medium", "kick": "high", "hihat": "medium", "bass": "high", "instruments": "medium"}
        gated, _ = gate_opening_vocal(act, v, 130.73, self.beat, kind="cue")
        self.assertEqual(color_from_stems(gated)[0], "yellow")

    def test_loops_need_the_vocal_to_carry_the_opening(self):
        beat = 60 / 122
        # 058 Chorus Loop @31.48: ~0.31 sung in the first 2 bars -> Kirill made it green.
        on = 0.31 * 8 * beat
        v = FakeVocal(envelope(100, [(31.73, 31.73 + on, 0.9)]))
        act = {"vocal": "medium", "kick": "high", "hihat": "low", "bass": "high", "instruments": "medium"}
        self.assertEqual(color_from_stems(gate_opening_vocal(act, v, 31.48, beat, "loop")[0])[0], "green")
        # 058 Vocal Groove Loop @7.87: ~0.83 sung -> stays yellow (Kirill kept it).
        v2 = FakeVocal(envelope(100, [(8.12, 8.12 + 0.83 * 8 * beat, 0.9)]))
        self.assertEqual(color_from_stems(gate_opening_vocal(act, v2, 7.87, beat, "loop")[0])[0], "yellow")

    def test_no_fine_data_leaves_activity_unchanged(self):
        class NoFine:
            voice_fine_frames = None
            voice_fine_reference = 0.0
        act = {"vocal": "medium"}
        self.assertEqual(gate_opening_vocal(act, NoFine(), 10.0, 0.5)[0], act)
        self.assertIsNone(vocal_opening_share(NoFine(), 10.0, 0.5))


# --------------------------------------------------------------------- R-88
class R88MoveToBreak(unittest.TestCase):
    def test_besomage_fresh_louder_line_on_phrase_one_stays_put(self):
        beat = 60 / 128
        t = 30.944
        # beat before ~0.5 of p95, beat after ~0.8: a new line lands on the press.
        v = FakeVocal(envelope(60, [(20, t, 0.5), (t, t + 6, 0.8)]))
        new_t, why = H.resolve_half_entered(v, t, beat)
        self.assertEqual(new_t, t)  # Kirill's spot, not the +1 bar move to 32.819
        self.assertEqual(why, "")

    def test_how_deep_line_running_out_through_press_is_skipped_never_moved_later(self):
        beat = 60 / 122
        t = 110.168
        # singing straight through the press and the two bars before (no clean earlier bar).
        v = FakeVocal(envelope(140, [(100, t + 0.3, 0.9), (t + 0.3, t + 3, 0.2)]))
        new_t, why = H.resolve_half_entered(v, t, beat)
        self.assertIsNone(new_t)  # Kirill deleted it; never 112.135 (later, off his phrase)
        self.assertIn("skipped", why)

    def test_moves_are_only_ever_earlier(self):
        beat = 0.5
        t = 40.0
        v = FakeVocal(envelope(80, [(37.0, 41.0, 0.9)]))
        new_t, _ = H.resolve_half_entered(v, t, beat)
        self.assertTrue(new_t is None or new_t <= t)


# --------------------------------------------------------------------- R-89
class R89KickGridWholeBeats(unittest.TestCase):
    def _clicks(self, bpm, seconds):
        sr = L.SR
        x = np.zeros(int(seconds * sr), dtype=np.float32)
        beat = 60.0 / bpm
        k = 0
        while k * beat < seconds:
            i = int(k * beat * sr)
            x[i: i + 400] = np.hanning(800)[:400] * 0.9
            k += 1
        return x, beat

    def test_kick_phase_on_whole_beats_has_no_fake_offset(self):
        # 137 Saving Up (130 BPM) failed every loop on kick_offset_ms 70-74 with a 1 s window:
        # the tail fold started 1 s mod 1 beat off the grid.
        for bpm in (130.0, 128.0, 100.0, 122.0):
            x, beat = self._clicks(bpm, 16 * 60 / bpm)
            half = 1.0
            whole = int(min(half, 4 * beat) / beat)
            span = int(whole * beat * L.SR)
            tail, head = x[-int(half * L.SR):], x[: int(half * L.SR)]
            kt, kh = L._kick_phase(tail[-span:], beat), L._kick_phase(head[:span], beat)
            d = abs(kt - kh)
            d = min(d, beat - d) * 1000
            self.assertLess(d, L.THRESHOLDS["kick_offset_ms_max"] / 4, bpm)

    def test_validate_loop_seam_passes_kick_grid_at_130_bpm(self):
        import os, shutil, tempfile
        from scipy.io import wavfile
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg missing")
        x, beat = self._clicks(130.0, 40.0)
        d = tempfile.mkdtemp()
        path = os.path.join(d, "clicks.wav")
        wavfile.write(path, L.SR, (x * 32767).astype(np.int16))
        for window in (1.0, 2.0):
            res = L.validate_loop_seam(path, 4 * beat, 16 * beat, beat_seconds=beat, window=window)
            self.assertNotIn("kick_grid", res.failures, res.summary())

    def test_loudness_threshold_matches_live_calibration(self):
        self.assertEqual(L.THRESHOLDS["loudness_db_max"], 6.0)

    def test_stem_envelope_seam_is_not_a_veto(self):
        from vdj_cuer import stems
        self.assertFalse(stems.STEM_SEAM_VETOES_LOOPS)


# --------------------------------------------------------------------- R-92
@dataclass(frozen=True)
class P:
    name: str
    position: float
    color_name: str
    elements: tuple = field(default=())


class R92OutroOnlyAtSongEnd(unittest.TestCase):
    colors = {"blue": "b", "green": "g", "yellow": "y", "orange": "o", "purple": "p"}

    def test_062_children_mid_song_drumless_cue_is_a_blue_break(self):
        # 062 Children, 198 s: Intro 7.74, Drop 15.49, last cue 100.65 (no drums), loops later.
        cues = [P("1", 7.74, "blue"), P("Beat Entry", 15.49, "green"), P("Build", 100.65, "blue", ("synth",))]
        loops = [P("Drum Loop", 131.61, "purple"), P("Groove Loop", 162.58, "green")]
        out, _ = name_from_stems(cues, loops, color_values=self.colors, first_one=7.74,
                                 song_length=198.0, beat=60 / 124)
        last = out[-1]
        self.assertNotEqual(last.name, "Outro")
        self.assertIn("Break", last.name)
        self.assertEqual(last.color_name, "blue")

    def test_058_american_dream_mid_song_drumless_cue_is_not_outro(self):
        # 058 American Dream, 244 s: only cue left @125.9 (was renamed Drop -> Outro).
        cues = [P("Drop", 125.9, "blue", ("piano",))]
        loops = [P("Hook Loop", 196.72, "yellow")]
        out, _ = name_from_stems(cues, loops, color_values=self.colors, first_one=7.87,
                                 song_length=244.0, beat=60 / 122)
        self.assertEqual(out[0].name, "Piano Break")
        self.assertFalse(is_song_ending(125.9, 244.0, beat=60 / 122))
        self.assertFalse(is_song_ending(100.65, 198.0, beat=60 / 124))

    def test_genuine_ending_still_outro(self):
        cues = [P("1", 0.0, "blue"), P("x", 60.0, "green"), P("y", 200.0, "blue")]
        out, _ = name_from_stems(cues, [], color_values=self.colors, first_one=0.0,
                                 song_length=240.0, beat=0.5)
        self.assertEqual(out[-1].name, "Outro")
        self.assertTrue(is_song_ending(200.0, 240.0, beat=0.5))
        self.assertFalse(is_song_ending(200.0, 240.0, beat=0.5, later_positions=[215.0]))


if __name__ == "__main__":
    unittest.main()
