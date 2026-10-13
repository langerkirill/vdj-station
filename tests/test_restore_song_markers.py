"""restore_song_markers: one song's cue/loop rows come back from a backup, nothing else moves."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import restore_song_markers as R
import vdj_database_safety as V

AUDIO = "/Users/x/Music/House/How Deep Is Your Love.flac"
HDR = '<?xml version="1.0" encoding="UTF-8"?>\n<VirtualDJ_Database Version="2026">\n'


def db_text(markers: str, comment: str, scan: str = '<Scan Bpm="0.491803" Phase="31.479275" />') -> bytes:
    text = (
        HDR
        + f' <Song FilePath="{AUDIO}" Flag="1">\n'
        '  <Tags Author="Calvin Harris" Title="How Deep Is Your Love" />\n'
        '  <Infos SongLength="212.0" />\n'
        + (f"  <Comment>{comment}</Comment>\n" if comment else "")
        + f"  {scan}\n"
        '  <Poi Pos="31.479275" Type="beatgrid" />\n'
        + markers
        + " </Song>\n"
        ' <Song FilePath="/Users/x/Music/House/Other.flac" Flag="1">\n'
        '  <Poi Name="Intro" Pos="1.000000" Num="1" Color="4278255360" Type="cue" />\n'
        " </Song>\n</VirtualDJ_Database>\n"
    )
    return text.replace("\n", "\r\n").encode()


HAND = (
    '  <Poi Name="Bass Loop" Pos="0.003883" Num="-1" Color="4278190335" Type="loop" Size="8.0" Slot="1" />\n'
    '  <Poi Name="Groove Loop" Pos="31.479275" Num="-1" Color="4278255360" Type="loop" Size="16.0" Slot="2" />\n'
    '  <Poi Name="Down Deep" Pos="149.511995" Num="-1" Color="4294934272" Type="loop" Size="8.0" Slot="3" />\n'
)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "database.xml"
        self.bak = self.root / "database.xml.backup.20261008_183650"
        self.bak.write_bytes(db_text(HAND, "green blue orange"))
        # after the empty AutoCue write: no loops, no Comment; Scan changed since the backup
        self.db.write_bytes(db_text("", "", scan='<Scan Bpm="0.491803" Phase="31.479275" Volume="1.0" />'))
        self._env = patch.dict(os.environ, {"MUSIC_SORTER_WRITE_BACKUP_DIR": str(self.root / "bk"),
                                            "VDJ_DB_VERIFIED_CACHE": str(self.root / "v.json")})
        self._env.start()
        os.environ.pop("MUSIC_SORTER_ALLOW_LF_DB", None)
        self._nvdj = patch.object(V, "is_virtualdj_running", return_value=False)
        self._nvdj.start()

    def tearDown(self):
        self._nvdj.stop()
        self._env.stop()
        os.environ["MUSIC_SORTER_ALLOW_LF_DB"] = "1"
        self._tmp.cleanup()

    def run_cli(self, *extra):
        with patch("builtins.print"):
            return R.main(["--song", AUDIO, "--database", str(self.db), *extra])

    def test_preview_and_find_do_not_write(self):
        before = self.db.read_bytes()
        self.assertEqual(self.run_cli("--find"), 0)
        self.assertEqual(self.run_cli("--backup", str(self.bak)), 0)
        self.assertEqual(self.db.read_bytes(), before)

    def test_apply_restores_only_this_songs_markers(self):
        self.assertEqual(self.run_cli("--backup", str(self.bak), "--apply"), 0)
        out = self.db.read_bytes()
        self.assertEqual(V.song_form_problems(out), [])
        text = out.decode()
        blk = R.song_block(text, AUDIO)
        self.assertEqual(R.describe(blk), R.describe(R.song_block(self.bak.read_bytes().decode(), AUDIO)))
        self.assertIn('Volume="1.0"', blk)  # current Scan kept, not the backup's
        self.assertIn("<Comment>green blue orange</Comment>", blk)
        self.assertLess(blk.index("<Comment>"), blk.index("<Scan"))
        other = R.song_block(text, "/Users/x/Music/House/Other.flac")
        self.assertIn('Name="Intro"', other)

    def test_backup_without_markers_refuses(self):
        self.bak.write_bytes(db_text("", ""))
        before = self.db.read_bytes()
        self.assertEqual(self.run_cli("--backup", str(self.bak), "--apply"), 1)
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
