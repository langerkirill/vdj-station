"""Detect VDJ vocal-stem digital holes while the original mix is still loud."""

from __future__ import annotations

import unittest

from pathlib import Path
import tempfile

from vdj_cuer.stem_vocal_holes import (
    VocalHole,
    audit_is_broken,
    cued_audio_paths_from_database,
    merge_flagged_hops,
    vocal_holes_from_rms,
)


class VocalHoleLogicTests(unittest.TestCase):
    def test_merges_adjacent_flagged_hops(self) -> None:
        holes = merge_flagged_hops([10.0, 10.5, 11.0, 20.0], hop=0.5)
        self.assertEqual(
            holes,
            [VocalHole(start=10.0, end=11.5), VocalHole(start=20.0, end=20.5)],
        )

    def test_ignores_vocal_silence_when_original_is_quiet(self) -> None:
        hop = 0.5
        n = 20
        vocal = [-240.0] * n
        orig = [-40.0] * n
        holes = vocal_holes_from_rms(vocal, orig, hop=hop)
        self.assertEqual(holes, [])

    def test_flags_ten_second_digital_mute_while_mix_is_loud(self) -> None:
        hop = 0.5
        # 4s ok, 10s mute, 4s ok
        vocal = [-20.0] * 8 + [-240.0] * 20 + [-20.0] * 8
        orig = [-8.0] * 36
        holes = vocal_holes_from_rms(vocal, orig, hop=hop)
        self.assertEqual(len(holes), 1)
        self.assertAlmostEqual(holes[0].start, 4.0)
        self.assertAlmostEqual(holes[0].end, 14.0)
        self.assertTrue(audit_is_broken(holes))

    def test_short_blip_is_not_broken(self) -> None:
        hop = 0.5
        vocal = [-20.0] * 10 + [-240.0] * 2 + [-20.0] * 10
        orig = [-8.0] * 22
        holes = vocal_holes_from_rms(vocal, orig, hop=hop)
        self.assertEqual(len(holes), 1)
        self.assertFalse(audit_is_broken(holes))

    def test_empty_inputs(self) -> None:
        self.assertEqual(vocal_holes_from_rms([], [], hop=0.5), [])
        self.assertFalse(audit_is_broken([]))

    def test_lists_stems_since_cutoff(self) -> None:
        from vdj_cuer.stem_vocal_holes import list_stem_audio_paths
        import os
        import time

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_audio = root / "old.flac"
            new_audio = root / "new.flac"
            old_audio.write_bytes(b"a")
            new_audio.write_bytes(b"b")
            (root / "old.flac.vdjstems").write_bytes(b"s")
            (root / "new.flac.vdjstems").write_bytes(b"s")
            past = time.time() - 90 * 24 * 3600
            os.utime(root / "old.flac.vdjstems", (past, past))
            paths = list_stem_audio_paths(root, since_unix=time.time() - 7 * 24 * 3600)
        self.assertEqual([p.name for p in paths], ["new.flac"])

    def test_cued_paths_skip_uncued_songs(self) -> None:
        xml = """
<VirtualDJ_Database>
 <Song FilePath="/lib/cued.flac">
  <Poi Pos="1" Num="1" Type="cue" />
 </Song>
 <Song FilePath="/lib/uncued.flac">
  <Poi Pos="0" Type="beatgrid" />
 </Song>
</VirtualDJ_Database>
"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            db.write_text(xml, encoding="utf-8")
            paths = cued_audio_paths_from_database(db)
        self.assertEqual(paths, ["/lib/cued.flac"])


if __name__ == "__main__":
    unittest.main()
