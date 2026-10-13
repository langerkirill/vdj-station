"""Loops end at or before the next cue (Kirill's own loops always do: 69/69 on the bench).

2026-10-09: the writer fitted loop length only to the song end (it passed an empty cue
list), so a 64-beat loop could run through the next section. With the stem-envelope seam
veto removed (bench/cloud/loop_gate_calib.py), this structural rule is what keeps loops
from straddling a section change.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import automatic_music_cuer_gemini as cuer_module
from vdj_cuer import loop_seam_dsp, stems
from vdj_cuer.common import WRITE_SCOPE_ALL

BEAT = 0.5  # 120 BPM


def cue(t, name):
    return {"timestamp": t, "elements": ["drums", "bass"], "cue_name": name, "color": "green", "confidence": 0.9}


def loop(t, beats):
    return {"start": t, "length_beats": beats, "elements": ["bass", "synth"], "loop_name": "Bass",
            "color": "blue", "confidence": 0.9, "seam": {"passed": True}}


class LoopFitTests(unittest.TestCase):
    def _prepare(self, cues, loops):
        with patch("builtins.print"):
            c = cuer_module.AutomaticMusicCuer(gemini_api_key="k", vdj_database_path="/tmp/database.xml")
        c.write_scope = WRITE_SCOPE_ALL
        analysis = {"measure_changes": cues, "loop_segments": loops}
        with patch.object(c, "_finalize_analysis_for_write", return_value=(analysis, 120.0, 400.0)), \
                patch.object(c, "get_beatgrid_offset", return_value=0.0), \
                patch.object(c, "validate_timing_hybrid", side_effect=lambda *a, **k: float(a[0])), \
                patch.object(c, "validate_color_assignment", return_value="green"), \
                patch.object(c, "create_loop_name", return_value="Bass"), \
                patch.object(c, "_actual_bpm", return_value=120.0), \
                patch.object(c, "_dsp_seam_ok", return_value=True), \
                patch.object(c, "_load_existing_prepared_pois", return_value=([], [])), \
                patch("builtins.print"):
            return c.prepare_song_cues("/music/track.flac", analysis)

    def test_long_loop_is_shortened_to_end_at_the_next_cue(self):
        # loop at 56 s asks for 64 beats (32 s); the next cue at 80 s is 48 beats later -> 32
        prepared = self._prepare([cue(80.0, "Break")], [loop(56.0, 64)])
        self.assertIn(80.0, [c.position for c in prepared.cues])
        self.assertEqual([(lp.position, lp.length_beats) for lp in prepared.loops], [(56.0, 32)])

    def test_loop_that_cannot_fit_16_beats_before_the_next_cue_is_dropped(self):
        prepared = self._prepare([cue(80.0, "Break")], [loop(76.0, 32)])  # only 8 beats left
        self.assertIn(80.0, [c.position for c in prepared.cues])
        self.assertEqual(prepared.loops, [])

    def test_loop_in_the_last_section_still_fits_to_song_end(self):
        prepared = self._prepare([cue(80.0, "Break")], [loop(80.0, 64)])  # no later cue
        self.assertEqual([(lp.position, lp.length_beats) for lp in prepared.loops], [(80.0, 64)])


class SeamCalibrationTests(unittest.TestCase):
    def test_stem_envelope_seam_no_longer_vetoes(self):
        # bench/cloud/loop_gate_calib.py: it passed 17/46 of Kirill's own loops and caught no
        # broken seam the DSP test misses (half-beat-off wraps 5/92 with or without it).
        self.assertFalse(stems.STEM_SEAM_VETOES_LOOPS)

    def test_dsp_loudness_jump_limit(self):
        self.assertEqual(loop_seam_dsp.THRESHOLDS["loudness_db_max"], 6.0)


if __name__ == "__main__":
    unittest.main()
