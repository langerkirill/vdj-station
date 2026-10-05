"""House copy-sort: sha1-verified true copy + locked Song clone, original untouched (temp DB only)."""

from __future__ import annotations

import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vdj_database_safety as V
from sorter import config as _config
from sorter import house_folders as hf
from sorter import relocate as R
from sorter import safe_write


def sha1(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def db_text(src: Path, *, uncued=False) -> bytes:
    pois = (
        '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
        if uncued
        else '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
        '  <Poi Name="Beat Entry" Pos="16.56" Num="1" Color="4294902015" Type="cue" />\r\n'
        '  <Poi Name="Build" Pos="228.08" Num="2" Color="4278255360" Type="cue" />\r\n'
        '  <Poi Name="Melody Loop" Pos="260.62" Num="-1" Color="4278190335" Type="loop" Size="8.0" Slot="2" />\r\n'
    )
    return (
        "<VirtualDJ_Database>\r\n"
        f'<Song FilePath="{src}" Flag="1">\r\n'
        '  <Tags Author="A" Title="T" Key="Am" User2="Inbox" Color="4278190335" />\r\n'
        '  <Scan Bpm="0.5" Volume="0.9" />\r\n'
        + pois
        + "</Song>\r\n"
        '<Song FilePath="/other/untouched.flac" Flag="1">\r\n'
        '  <Poi Name="Intro" Pos="1.0" Num="1" Color="4278255360" Type="cue" />\r\n'
        "</Song>\r\n"
        "</VirtualDJ_Database>\r\n"
    ).encode()


class HouseCopySortTests(unittest.TestCase):
    def setUp(self) -> None:
        safe_write.reset_session_for_tests()
        self._t = tempfile.TemporaryDirectory()
        root = Path(self._t.name)
        self.house = root / "House"
        (self.house / "Amped").mkdir(parents=True)
        (self.house / "Chill").mkdir()
        self.ready = root / "Ready"
        self.ready.mkdir()
        self.src = (self.ready / "Song One.flac").resolve()
        self.src.write_bytes(b"AUDIO-BYTES" * 1000)
        Path(f"{self.src}.vdjstems").write_bytes(b"stems")
        self.db = root / "database.xml"
        self.db.write_bytes(db_text(self.src))
        self.notes = root / "notes"
        self.notes.mkdir()
        self.bk = root / "bk"
        import os

        self._env = patch.dict(os.environ, {"MUSIC_SORTER_WRITE_BACKUP_DIR": str(self.bk)})
        self._env.start()
        self._libs = patch.dict(_config.LIBRARIES, {"House": self.house}, clear=True)
        self._libs.start()
        from sorter import library as _library

        self._lib2 = patch.object(_library, "LIBRARIES", _config.LIBRARIES)
        self._lib2.start()
        self._notes = patch.object(_config, "DJ_NOTES_ROOT", self.notes)
        self._notes.start()
        self._run = patch.object(R, "is_virtualdj_running", return_value=False)
        self._run.start()

    def tearDown(self) -> None:
        for p in (self._run, self._notes, self._lib2, self._libs, self._env):
            p.stop()
        self._t.cleanup()

    def sort(self, rel="Amped", **kw):
        dests = [{"library": "House", "relative_folder": rel, **kw.pop("dest_extra", {})}]
        return R.sort_track(
            self.src, library_name="House", relative_folder=rel, destinations=dests,
            database_path=self.db, ready_root=self.ready, **kw,
        )

    def poi_counts(self, text, path):
        s, e = V._find_song_span(text, V.normalize_database_path(str(path)))
        blk = text[s:e]
        return (
            len(re.findall(r'Type="cue"', blk)),
            len(re.findall(r'Type="loop"', blk)),
            len(re.findall(r'Type="beatgrid"', blk)),
            blk,
        )

    def test_copy_sort_true_copy_clone_round_trip(self):
        before_src = self.src.read_bytes()
        before_db = self.db.read_bytes().decode()
        res = self.sort("Amped")
        dest = (self.house / "Amped" / "Song One.flac").resolve()
        self.assertTrue(res.copied and res.original_kept and res.saved)
        self.assertEqual(sha1(dest), sha1(self.src))
        self.assertEqual(res.sha1, sha1(self.src))
        self.assertEqual(self.src.read_bytes(), before_src)  # original untouched
        self.assertTrue(Path(f"{dest}.vdjstems").is_file())
        self.assertTrue(Path(f"{self.src}.vdjstems").is_file())
        text = V.read_vdj_database_text(self.db)
        sc, sl, sg, sblk = self.poi_counts(text, self.src)
        dc, dl, dg, dblk = self.poi_counts(text, dest)
        self.assertEqual((sc, sl, sg), (dc, dl, dg))
        self.assertEqual((dc, dl, dg), (2, 1, 1))
        self.assertIn('Color="4278190335"', dblk)  # song color kept
        self.assertIn('Bpm="0.5"', dblk)  # beatgrid/scan kept
        self.assertIn('User2="Amped"', dblk)  # destination folder, not the source's "Inbox"
        self.assertIn('User2="Inbox"', sblk)  # source entry unchanged
        # nothing else changed: source Song + other Song byte-identical, only an appended Song
        self.assertEqual(text.count("<Song "), 3)
        self.assertTrue(text.startswith(before_db.split("</VirtualDJ_Database>")[0]))
        self.assertEqual(len(list(self.bk.iterdir())), 1)  # single backup-first

    def test_second_sort_skips_existing_without_duplicate(self):
        self.sort("Amped")
        before = self.db.read_bytes()
        res = self.sort("Amped")
        self.assertFalse(res.copied)
        self.assertTrue(res.skipped_existing)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(V.read_vdj_database_text(self.db).count("<Song "), 3)

    def test_different_file_at_destination_is_refused_not_overwritten(self):
        d = self.house / "Amped" / "Song One.flac"
        d.write_bytes(b"someone else's file")
        with self.assertRaises(FileExistsError):
            self.sort("Amped")
        self.assertEqual(d.read_bytes(), b"someone else's file")
        self.assertEqual(V.read_vdj_database_text(self.db).count("<Song "), 2)

    def test_dry_run_changes_nothing(self):
        before = self.db.read_bytes()
        res = self.sort("Amped", dry_run=True)
        self.assertTrue(res.dry_run and res.original_kept and not res.copied)
        self.assertFalse((self.house / "Amped" / "Song One.flac").exists())
        self.assertEqual(self.db.read_bytes(), before)

    def test_uncued_track_is_refused(self):
        self.db.write_bytes(db_text(self.src, uncued=True))
        with self.assertRaises(PermissionError):
            self.sort("Amped")
        self.assertFalse((self.house / "Amped" / "Song One.flac").exists())

    def test_vdj_running_is_refused_before_any_copy(self):
        with patch.object(R, "is_virtualdj_running", return_value=True):
            with self.assertRaises(RuntimeError):
                self.sort("Amped")
        self.assertFalse((self.house / "Amped" / "Song One.flac").exists())

    def test_missing_folder_without_new_flag_is_refused(self):
        with self.assertRaises(Exception):
            self.sort("Brand New")
        self.assertFalse((self.house / "Brand New").exists())

    def test_new_folder_created_validated_and_counted(self):
        res = self.sort("Sunrise", dest_extra={"new_folder": True})
        self.assertTrue(res.new_folder_created and res.copied)
        self.assertTrue((self.house / "Sunrise" / "Song One.flac").is_file())
        self.assertEqual(hf.new_folder_state()["count"], 1)
        self.assertEqual(hf.new_folder_state()["folders"], ["Sunrise"])

    def test_new_flag_on_an_existing_folder_never_blocks_the_sort(self):
        # stale "new folder" flag (first sort created it) / different letter case / same leaf elsewhere
        for dest in ("Amped", "amped", "AMPED"):
            self.assertEqual(hf.resolve_destination_rel(dest, new_folder=True), "Amped")
        (self.house / "Chill" / "Mystical").mkdir()
        self.assertEqual(hf.resolve_destination_rel("Mystical", new_folder=True), "Chill/Mystical")
        self.assertEqual(hf.resolve_destination_rel("chill/mystical", new_folder=True), "Chill/Mystical")
        self.assertEqual(hf.resolve_destination_rel("Ampeds", new_folder=True), "Amped")  # similar (plural)
        res = self.sort("amped", dest_extra={"new_folder": True})
        self.assertTrue(res.copied)
        self.assertTrue((self.house / "Amped" / "Song One.flac").is_file())
        self.assertEqual(sorted(d.name for d in self.house.iterdir()), ["Amped", "Chill"])  # no stray folder
        self.assertEqual(hf.new_folder_state()["count"], 0)  # nothing created, nothing counted

    def test_claim_of_an_existing_folder_is_a_no_op(self):
        path = hf.claim_new_folder("Amped")
        self.assertEqual(path, self.house / "Amped")
        self.assertEqual(hf.new_folder_state()["count"], 0)

    def test_new_folder_name_validation(self):
        for bad in ("a/b", "..", "x..y", ".hidden", "", "a:b"):
            with self.assertRaises(ValueError, msg=bad):
                hf.resolve_destination_rel(bad, new_folder=True)
        with self.assertRaises(Exception):
            hf.resolve_destination_rel("Nope/Child", new_folder=True)  # parent must exist
        self.assertEqual(hf.resolve_destination_rel("Amped/Deep", new_folder=True), "Amped/Deep")

    def test_no_cap_on_new_folders(self):
        for n in ("N1", "N2", "N3", "N4", "N5", "Piano House"):
            hf.claim_new_folder(n)
        st = hf.new_folder_state()
        self.assertEqual(st["count"], 6)
        self.assertIsNone(st["remaining"])
        self.assertTrue(st["unlimited"])
        self.assertTrue((self.house / "Piano House").is_dir())
        # and a sort into yet another new folder is not refused
        hf.assert_new_folder_allowed()

    def test_failed_clone_rolls_back_copy_and_new_folder(self):
        with patch.object(R, "safe_clone_song", side_effect=safe_write.SaveFailed("boom")):
            with self.assertRaises(RuntimeError):
                self.sort("Sunrise", dest_extra={"new_folder": True})
        self.assertFalse((self.house / "Sunrise").exists())
        self.assertEqual(hf.new_folder_state()["count"], 0)
        self.assertTrue(self.src.is_file())

    def test_copy_file_verified_sha1_mismatch_removes_partial(self):
        real = R._sha1_file
        calls = {"n": 0}

        def bad(p):
            calls["n"] += 1
            return real(p) if calls["n"] == 1 else "deadbeef"

        d = self.house / "Chill" / "x.flac"
        with patch.object(R, "_sha1_file", bad):
            with self.assertRaises(RuntimeError):
                R.copy_file_verified(self.src, d)
        self.assertFalse(d.exists())
        self.assertEqual(list((self.house / "Chill").iterdir()), [])
        self.assertTrue(self.src.is_file())


if __name__ == "__main__":
    unittest.main()
