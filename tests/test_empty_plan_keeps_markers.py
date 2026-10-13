"""AutoCue never replaces a song's markers with nothing.

Kirill 2026-10-08: re-cueing "How Deep Is Your Love" (write scope all) found 0 cues and
0 loops and still rewrote the song, which removed 3 loops he had made by hand.
Rules: a side (cues or loops) the new plan has nothing for keeps what is on disk, and a
plan with nothing new at all writes nothing.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from vdj_cuer.marker_guard import _fp_of
from unittest.mock import patch

import automatic_music_cuer_gemini as cuer_module
from vdj_cuer.common import WRITE_SCOPE_ALL, WRITE_SCOPE_CUES, WRITE_SCOPE_LOOPS
from vdj_cuer.cue_writer import PreparedPoi

HAND_LOOP = PreparedPoi(
    kind="loop", name="Down Deep", position=149.511995, color_name="orange",
    color_value="4294934272", elements=["preserved"], length_beats=8.0,
)
HAND_CUE = PreparedPoi(
    kind="cue", name="Intro", position=1.0, color_name="blue",
    color_value="4278190335", elements=["preserved"],
)
CUE = {"timestamp": 32.0, "elements": ["drums"], "cue_name": "Groove", "color": "green", "confidence": 0.9}
LOOP = {
    "start": 64.0, "length_beats": 16, "elements": ["bass", "synth"], "loop_name": "Bass",
    "color": "blue", "confidence": 0.9, "seam": {"passed": True},
}


class EmptyPlanTests(unittest.TestCase):
    def _prepare(self, scope, cues, loops, existing=([HAND_CUE], [HAND_LOOP])):
        with patch("builtins.print"):
            cuer = cuer_module.AutomaticMusicCuer(gemini_api_key="k", vdj_database_path="/tmp/database.xml")
        cuer.write_scope = scope
        analysis = {"measure_changes": list(cues), "loop_segments": list(loops)}
        with patch.object(cuer, "_finalize_analysis_for_write", return_value=(analysis, 120.0, 200.0)), \
                patch.object(cuer, "get_beatgrid_offset", return_value=0.0), \
                patch.object(cuer, "validate_timing_hybrid", side_effect=lambda *a, **k: float(a[0])), \
                patch.object(cuer, "validate_color_assignment", return_value="green"), \
                patch.object(cuer, "create_loop_name", return_value="Bass"), \
                patch.object(cuer, "_load_existing_prepared_pois", return_value=existing), \
                patch("vdj_cuer.marker_guard.last_written", return_value={
                    # R-88: these markers are AutoCue's own untouched write, so it may replace them.
                    "source": "test", "markers": [_fp_of(m) for m in existing[0] + existing[1]]}), \
                patch("builtins.print"):
            return cuer, cuer.prepare_song_cues("/music/track.flac", analysis)

    def test_all_scope_nothing_found_writes_nothing(self):
        _cuer, prepared = self._prepare(WRITE_SCOPE_ALL, [], [])
        self.assertTrue(prepared.nothing_to_write)

    def test_all_scope_no_loops_keeps_existing_loops(self):
        _cuer, prepared = self._prepare(WRITE_SCOPE_ALL, [CUE], [])
        self.assertFalse(prepared.nothing_to_write)
        self.assertIn("Groove", [c.name for c in prepared.cues])
        self.assertNotIn("Intro", [c.name for c in prepared.cues])
        self.assertEqual([(lp.name, lp.position, lp.length_beats) for lp in prepared.loops],
                         [("Down Deep", 149.511995, 8.0)])

    def test_all_scope_no_cues_keeps_existing_cues(self):
        _cuer, prepared = self._prepare(WRITE_SCOPE_ALL, [], [LOOP])
        self.assertFalse(prepared.nothing_to_write)
        self.assertEqual([c.name for c in prepared.cues], ["Intro"])
        self.assertTrue(prepared.loops)
        self.assertNotIn("Down Deep", [lp.name for lp in prepared.loops])

    def test_all_scope_with_both_still_replaces_both(self):
        _cuer, prepared = self._prepare(WRITE_SCOPE_ALL, [CUE], [LOOP])
        self.assertFalse(prepared.nothing_to_write)
        self.assertNotIn("Intro", [c.name for c in prepared.cues])
        self.assertNotIn("Down Deep", [lp.name for lp in prepared.loops])

    def test_cues_scope_without_cues_writes_nothing(self):
        _cuer, prepared = self._prepare(WRITE_SCOPE_CUES, [], [LOOP])
        self.assertTrue(prepared.nothing_to_write)

    def test_loops_scope_without_loops_writes_nothing(self):
        _cuer, prepared = self._prepare(WRITE_SCOPE_LOOPS, [CUE], [])
        self.assertTrue(prepared.nothing_to_write)


class EmptyPlanDatabaseTests(unittest.TestCase):
    """End to end on a temp database.xml: the song's bytes stay exactly as they were."""

    def test_empty_plan_leaves_database_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            audio = "/Users/x/Music/House/How Deep Is Your Love.flac"
            raw = (
                '<?xml version="1.0" encoding="UTF-8"?>\r\n<VirtualDJ_Database Version="2026">\r\n'
                f' <Song FilePath="{audio}" Flag="1">\r\n'
                '  <Tags Author="Calvin Harris" Title="How Deep Is Your Love" />\r\n'
                '  <Infos SongLength="212.0" />\r\n'
                '  <Scan Bpm="0.491803" Phase="31.479275" />\r\n'
                '  <Poi Name="Bass Loop" Pos="0.003883" Num="-1" Color="4278190335" Type="loop" Size="8.0" Slot="1" />\r\n'
                '  <Poi Name="Down Deep" Pos="149.511995" Num="-1" Color="4294934272" Type="loop" Size="8.0" Slot="2" />\r\n'
                " </Song>\r\n</VirtualDJ_Database>\r\n"
            ).encode()
            db.write_bytes(raw)
            with patch("builtins.print"):
                cuer = cuer_module.AutomaticMusicCuer(gemini_api_key="k", vdj_database_path=str(db))
            cuer.write_scope = WRITE_SCOPE_ALL
            analysis = {"measure_changes": [], "loop_segments": []}
            with patch.object(cuer, "_finalize_analysis_for_write", return_value=(analysis, 122.0, 212.0)), \
                    patch.object(cuer, "get_beatgrid_offset", return_value=31.479275), \
                    patch("vdj_database_safety.is_virtualdj_running", return_value=False), \
                    patch.dict(os.environ, {"VDJ_ALLOW_RUNNING_WRITES": "1"}), \
                    patch("vdj_cuer.cue_writer.rewrite_song_xml_in_database") as rewrite, \
                    patch("builtins.print"):
                applied = cuer._apply_cues_to_database(audio, analysis)
            self.assertFalse(applied)
            rewrite.assert_not_called()
            self.assertEqual(db.read_bytes(), raw)
            self.assertIn("nothing was written", cuer.last_apply_note)


if __name__ == "__main__":
    unittest.main()
