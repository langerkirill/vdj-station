"""House-fork recommender: existing-folder snapping, new-folder proposals (cap 3)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import house_folders
from sorter.recommend import (
    HouseFolderPickSchema,
    _pick_from_schema,
    build_folder_catalog,
    build_prompt,
    snap_to_approved,
)


class ExistingFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.house = Path(self._tmp.name) / "House"
        for d in ("Amped", "Bassy", "Chill/Journey", "Energy/Light"):
            (self.house / d).mkdir(parents=True)
        self._notes = tempfile.TemporaryDirectory()
        self._p1 = patch.object(house_folders, "house_root", lambda: self.house)
        self._p2 = patch.object(
            house_folders._config, "DJ_NOTES_ROOT", Path(self._notes.name)
        )
        self._p1.start()
        self._p2.start()

    def tearDown(self) -> None:
        self._p1.stop()
        self._p2.stop()
        self._tmp.cleanup()
        self._notes.cleanup()

    def test_catalog_is_the_real_house_subfolders_no_oh(self):
        cat = build_folder_catalog()
        self.assertEqual(list(cat), ["House"])
        self.assertEqual(
            cat["House"], ["Amped", "Bassy", "Chill", "Chill/Journey", "Energy", "Energy/Light"]
        )
        self.assertFalse(any(n.startswith("OH ") for n in cat["House"]))

    def test_snap_exact_and_leaf(self):
        self.assertEqual(snap_to_approved("amped"), "Amped")
        self.assertEqual(snap_to_approved("energy/light"), "Energy/Light")
        self.assertEqual(snap_to_approved("Journey"), "Chill/Journey")
        self.assertIsNone(snap_to_approved("Nonexistent"))
        self.assertIsNone(snap_to_approved(""))

    def test_pick_rejects_unknown(self):
        with self.assertRaises(ValueError):
            _pick_from_schema(
                HouseFolderPickSchema(relative_path="Zzz", confidence=0.5, reasoning="x")
            )

    def test_pick_snaps_alternatives(self):
        pick = _pick_from_schema(
            HouseFolderPickSchema(
                relative_path="Amped",
                confidence=0.7,
                reasoning="big",
                alternatives=["bassy", "Amped", "bogus"],
            )
        )
        self.assertEqual(pick.relative_path, "Amped")
        self.assertEqual(pick.alternatives, ["Bassy"])
        self.assertFalse(pick.new_folder)

    def test_new_folder_only_when_nothing_fits_and_validated(self):
        pick = _pick_from_schema(
            HouseFolderPickSchema(
                relative_path="",
                confidence=0.4,
                reasoning="nothing fits",
                use_new_folder=True,
                new_folder_name="Sunrise",
            )
        )
        self.assertTrue(pick.new_folder)
        self.assertEqual(pick.relative_path, "Sunrise")
        # existing name proposed as "new" -> falls back (no alternatives -> error)
        with self.assertRaises(ValueError):
            _pick_from_schema(
                HouseFolderPickSchema(
                    relative_path="", confidence=0.4, reasoning="x",
                    use_new_folder=True, new_folder_name="Amped",
                )
            )
        # an existing folder always wins over a new proposal
        pick = _pick_from_schema(
            HouseFolderPickSchema(
                relative_path="Bassy", confidence=0.9, reasoning="x",
                use_new_folder=True, new_folder_name="Sunrise",
            )
        )
        self.assertEqual((pick.relative_path, pick.new_folder), ("Bassy", False))

    def test_new_folder_proposals_are_allowed_without_a_cap(self):
        (Path(self._notes.name) / house_folders.NEW_FOLDERS_FILE).write_text(
            '{"folders": ["A1", "A2", "A3", "A4", "A5"]}'
        )
        pick = _pick_from_schema(
            HouseFolderPickSchema(
                relative_path="", confidence=0.4, reasoning="x",
                use_new_folder=True, new_folder_name="Sunrise",
            )
        )
        self.assertTrue(pick.new_folder)
        self.assertNotIn("NOT allowed", build_prompt("a.mp3", 121.0, ["Amped"]))

    def test_prompt_mentions_house_not_zouk(self):
        text = build_prompt("a.mp3", 121.0, build_folder_catalog()["House"]).lower()
        self.assertIn("organic house", text)
        self.assertIn("- amped", text)
        self.assertIn("original stays", text)
        self.assertNotIn("zouk", text)
        self.assertNotIn("oh warm groove", text)


if __name__ == "__main__":
    unittest.main()
