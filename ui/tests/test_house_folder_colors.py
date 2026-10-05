"""Song-level color per House subfolder (house_folder_colors.json)."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vdj_database_safety as V
from sorter import cue_edit, house_colors, safe_write

SCHEMA = {
    "Bassy": {"name": "Orange", "hex": "#ff8800", "vdj_value": "4294934272"},
    "Vocal": {"name": "Blue", "hex": "#0000FF", "vdj_value": 4278190335},
    "default": {"name": "Purple", "hex": "#8000FF", "vdj_value": "4288020735"},
    "Broken": {"name": "x", "hex": "nope", "vdj_value": "not-a-number"},
}

DB = (
    '<?xml version="1.0" encoding="UTF-8"?>\r\n<VirtualDJ_Database Version="2026">\r\n'
    ' <Song FilePath="/src/a.flac" FileSize="1">\r\n'
    '  <Tags Author="A" Title="T" User2="Zouk" />\r\n'
    '  <Infos SongLength="200.0" Bitrate="320" Cover="1" />\r\n'
    '  <Scan Bpm="0.5" />\r\n'
    '  <Poi Name="Intro" Pos="1.0" Num="1" Color="4278255360" Type="cue" />\r\n'
    " </Song>\r\n"
    "</VirtualDJ_Database>\r\n"
).encode()


class HouseColorTable(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._before = house_colors.config.DJ_NOTES_ROOT
        house_colors.config.DJ_NOTES_ROOT = Path(self._tmp.name)

    def tearDown(self):
        house_colors.config.DJ_NOTES_ROOT = self._before
        self._tmp.cleanup()

    def _write(self, data=SCHEMA):
        house_colors.colors_path().write_text(json.dumps(data), encoding="utf-8")

    def test_longest_listed_prefix_wins_and_deeper_folders_inherit(self):
        self._write({
            "Chill": {"name": "A", "hex": "#111111", "vdj_value": 1},
            "Chill/Journey": {"name": "B", "hex": "#222222", "vdj_value": 2},
            "_default": {"name": "Silver", "hex": "#BFBFBF", "vdj_value": 3},
        })
        f = house_colors.user_color_for_folder
        self.assertEqual(f("Chill"), "1")
        self.assertEqual(f("Chill/Journey"), "2")
        self.assertEqual(f("Chill/Journey/low_quality_backups"), "2")
        self.assertEqual(f("Chill/Other/deep"), "1")
        self.assertEqual(f("Brand New"), "3")  # unknown folder -> silver default

    def test_real_color_file_parses_if_present(self):
        real = Path.home() / "Music/DJ/Notes/House-8788/house_folder_colors.json"
        if not real.is_file():
            self.skipTest("real colour file not present")
        house_colors.config.DJ_NOTES_ROOT = real.parent  # read-only use
        t = house_colors.load()
        self.assertTrue(t["exists"])
        self.assertIsNotNone(t["default"])  # "_default" silver
        self.assertGreaterEqual(len(t["folders"]), 30 - 1)
        for k, e in t["folders"].items():
            self.assertRegex(e["hex"], r"^#[0-9A-F]{6}$", k)
            self.assertTrue(0 <= int(e["vdj_value"]) <= 0xFFFFFFFF, k)

    def test_static_ui_wiring_for_legend_and_chips(self):
        root = Path(__file__).resolve().parents[1] / "static"
        js = (root / "app.js").read_text(encoding="utf-8")
        html = (root / "index.html").read_text(encoding="utf-8")
        css = (root / "styles.css").read_text(encoding="utf-8")
        for needle in ("/api/house-folder-colors", "renderHouseColorLegend", "houseColorStyle", "loadHouseColors"):
            self.assertIn(needle, js)
        self.assertIn('id="houseColorLegend"', html)
        self.assertIn("house-color", css)

    def test_missing_file_means_no_color(self):
        self.assertFalse(house_colors.load()["exists"])
        self.assertIsNone(house_colors.user_color_for_folder("Bassy"))

    def test_schema_default_hex_and_nesting(self):
        self._write()
        t = house_colors.load()
        self.assertEqual(sorted(t["folders"]), ["Bassy", "Vocal"])  # broken entry dropped
        self.assertEqual(t["folders"]["Bassy"]["hex"], "#FF8800")
        self.assertEqual(t["folders"]["Vocal"]["vdj_value"], "4278190335")
        self.assertEqual(house_colors.user_color_for_folder("bassy"), "4294934272")
        self.assertEqual(house_colors.user_color_for_folder("Bassy/Deep"), "4294934272")
        self.assertEqual(house_colors.user_color_for_folder("Brand New"), "4288020735")  # default
        self.assertIsNone(house_colors.user_color_for_folder(""))

    def test_hex_is_derived_from_vdj_value_when_missing(self):
        self._write({"X": {"name": "n", "vdj_value": "4294934272"}})
        self.assertEqual(house_colors.load()["folders"]["X"]["hex"], "#FF7F00")


class CloneSetsSongColor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = patch.dict(os.environ, {"MUSIC_SORTER_WRITE_BACKUP_DIR": str(self.root / "bk"),
                                            "MUSIC_SORTER_FOREIGN_RETRY_BUDGET_S": "0"})
        self._env.start()
        os.environ.pop("MUSIC_SORTER_ALLOW_LF_DB", None)
        safe_write.reset_session_for_tests()
        self.db = self.root / "database.xml"
        self.db.write_bytes(DB)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_single_clone_gets_infos_usercolor_in_vdj_form(self):
        safe_write.safe_clone_song(self.db, "/src/a.flac", "/House/Bassy/a.flac", user_color="4294934272")
        raw = self.db.read_bytes()
        V.assert_crlf_bytes(raw)
        self.assertEqual(V.song_form_problems(raw), [])
        rows = re.findall(rb" <Song .*?</Song>", raw, re.S)
        self.assertEqual(len(rows), 2)
        self.assertNotIn(b"UserColor", rows[0])  # source untouched
        self.assertIn(b'<Infos SongLength="200.0" Bitrate="320" UserColor="4294934272" Cover="1" />', rows[1])

    def test_house_and_sauna_fest_copies_both_get_the_house_folder_color(self):
        safe_write.safe_clone_songs(
            self.db,
            "/src/a.flac",
            [("/House/Bassy/a.flac", None), ("/Sets/Sauna Fest/Bassy/a.flac", "/House/Bassy/a.flac")],
            user_color="4294934272",
        )
        raw = self.db.read_bytes()
        self.assertEqual(raw.count(b'UserColor="4294934272"'), 2)
        self.assertEqual(V.song_form_problems(raw), [])
        V.assert_crlf_bytes(raw)

    def test_no_color_when_none(self):
        safe_write.safe_clone_song(self.db, "/src/a.flac", "/House/X/a.flac")
        self.assertNotIn(b"UserColor", self.db.read_bytes())


class SortWiring(unittest.TestCase):
    def test_house_sort_and_sauna_mirror_pass_the_folder_color(self):
        src = (Path(__file__).resolve().parents[1] / "sorter" / "relocate.py").read_text(encoding="utf-8")
        self.assertIn("user_color=_house_user_color(rel)", src)
        self.assertIn("user_color=_house_user_color(house_rel)", src)


if __name__ == "__main__":
    unittest.main()


class NewFolderGetsDistinctColor(unittest.TestCase):
    def test_new_folder_gets_its_own_unused_color_and_existing_ones_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = house_colors.config.DJ_NOTES_ROOT
            house_colors.config.DJ_NOTES_ROOT = Path(tmp)
            try:
                house_colors.colors_path().write_text(json.dumps(SCHEMA), encoding="utf-8")
                e = house_colors.ensure_folder_color("Piano House")
                self.assertIsNotNone(e)
                data = json.loads(house_colors.colors_path().read_text(encoding="utf-8"))
                self.assertIn("Piano House", data)
                self.assertEqual(data["Bassy"], SCHEMA["Bassy"])  # untouched
                self.assertNotIn(e["hex"], [v["hex"].upper() for k, v in SCHEMA.items() if k != "Broken"])
                self.assertEqual(house_colors.user_color_for_folder("Piano House"), e["vdj_value"])
                # a second call never re-colors it
                self.assertEqual(house_colors.ensure_folder_color("piano house")["vdj_value"], e["vdj_value"])
            finally:
                house_colors.config.DJ_NOTES_ROOT = before
