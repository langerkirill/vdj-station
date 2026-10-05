"""'Add to Sauna Fest' (Sets/Sauna Fest + House copy) and the Add Cues 'sorted' list-leave state."""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import vdj_database_safety as V
from sorter import config as _config
from sorter import relocate as R
from sorter import safe_write
from sorter import sorted_state as SS

from test_house_copy_sort import HouseCopySortTests, db_text, sha1


class SaunaFestTests(unittest.TestCase):
    def setUp(self) -> None:
        HouseCopySortTests.setUp(self)  # temp House, Ready, db, backups, notes
        self.sets = Path(self._t.name) / "Sets"
        self.sets.mkdir()
        self.sauna = self.sets / "Sauna Fest"  # NOT created: first copy-in must create it
        self._p = [
            patch.object(_config, "SETS_ROOT", self.sets),
            patch.object(_config, "SAUNA_FEST_SET_DIR", self.sauna),
            patch.object(R, "SETS_ROOT", self.sets),
            patch.object(R, "READY_FOR_SORT", self.ready),
        ]
        for p in self._p:
            p.start()

    def tearDown(self) -> None:
        for p in self._p:
            p.stop()
        HouseCopySortTests.tearDown(self)

    def go(self, rel="Amped", **kw):
        dests = [{"library": "House", "relative_folder": rel, **kw.pop("dest_extra", {})}]
        return R.sauna_fest_sort_track(
            self.src, relative_folder=rel, destinations=dests, database_path=self.db, **kw
        )

    def song_block(self, path):
        text = V.read_vdj_database_text(self.db)
        s, e = V._find_song_span(text, V.normalize_database_path(str(path)))
        return text[s:e]

    def test_both_copies_registered_with_cues_and_house_user2(self):
        before_src = self.src.read_bytes()
        res = self.go("Amped")
        house = (self.house / "Amped" / "Song One.flac").resolve()
        sets = (self.sauna / "Amped" / "Song One.flac").resolve()
        self.assertTrue(res.saved and res.original_kept)
        for dest in (house, sets):
            self.assertEqual(sha1(dest), sha1(self.src))
            self.assertTrue(Path(f"{dest}.vdjstems").is_file())
        self.assertEqual(self.src.read_bytes(), before_src)
        hb, sb = self.song_block(house), self.song_block(sets)
        for blk in (hb, sb):
            self.assertEqual(len(re.findall(r'Type="cue"', blk)), 2)
            self.assertEqual(len(re.findall(r'Type="loop"', blk)), 1)
            self.assertIn('Bpm="0.5"', blk)
            self.assertIn('Color="4278190335"', blk)
        # User2: House folder label for BOTH (set copy follows its origin crate, never "Sets")
        self.assertIn('User2="Amped"', hb)
        self.assertIn('User2="Amped"', sb)
        self.assertEqual(V.read_vdj_database_text(self.db).count("<Song "), 4)
        self.assertEqual(len(list(self.bk.iterdir())), 1)  # ONE backup, ONE write

    def test_set_copy_mirrors_nested_house_folder_and_rolls_back_all_levels(self):
        (self.house / "Chill" / "Mystical").mkdir()
        before = self.db.read_bytes()
        with patch("sorter.safe_write._guards", side_effect=safe_write.SaveFailed("boom")):
            with self.assertRaises(RuntimeError):
                self.go("Chill/Mystical")
        self.assertFalse(self.sauna.exists())  # Sauna Fest/Chill/Mystical all removed again
        self.assertEqual(self.db.read_bytes(), before)
        self.go("Chill/Mystical")
        self.assertTrue((self.sauna / "Chill" / "Mystical" / "Song One.flac").is_file())
        self.assertIn('User2="Chill/Mystical"', self.song_block((self.sauna / "Chill" / "Mystical" / "Song One.flac").resolve()))

    def test_new_house_folder_is_mirrored_under_sauna_fest(self):
        self.go("Hypnotic", dest_extra={"new_folder": True})
        self.assertTrue((self.house / "Hypnotic" / "Song One.flac").is_file())
        self.assertTrue((self.sauna / "Hypnotic" / "Song One.flac").is_file())

    def test_new_folders_are_not_capped_server_side(self):
        from sorter import house_folders as hf

        for n in ("N1", "N2", "N3", "N4"):
            hf.claim_new_folder(n)
        self.go("Hypnotic", dest_extra={"new_folder": True})
        self.assertTrue((self.house / "Hypnotic" / "Song One.flac").is_file())

    def test_second_run_skips_without_duplicates(self):
        self.go("Amped")
        before = self.db.read_bytes()
        res = self.go("Amped")
        self.assertTrue(res.skipped_existing and not res.copied)
        self.assertEqual(self.db.read_bytes(), before)

    def test_different_file_in_set_folder_is_refused_before_anything_is_copied(self):
        (self.sauna / "Amped").mkdir(parents=True)
        other = self.sauna / "Amped" / "Song One.flac"
        other.write_bytes(b"not the same")
        before = self.db.read_bytes()
        with self.assertRaises(FileExistsError):
            self.go("Amped")
        self.assertEqual(other.read_bytes(), b"not the same")
        self.assertFalse((self.house / "Amped" / "Song One.flac").exists())
        self.assertEqual(self.db.read_bytes(), before)

    def test_db_failure_rolls_back_files_and_new_set_folder(self):
        before = self.db.read_bytes()
        with patch("sorter.safe_write._guards", side_effect=safe_write.SaveFailed("foreign writer", code="foreign_writer")):
            with self.assertRaises(RuntimeError):
                self.go("Amped")
        self.assertFalse((self.house / "Amped" / "Song One.flac").exists())
        self.assertFalse(self.sauna.exists())  # the folder this call created is removed again
        self.assertEqual(self.db.read_bytes(), before)
        self.assertTrue(self.src.is_file())

    def test_failed_registration_keeps_preexisting_folder_and_files(self):
        self.sauna.mkdir()
        keep = self.sauna / "keep.flac"
        keep.write_bytes(b"x")
        with patch("sorter.safe_write._guards", side_effect=safe_write.SaveFailed("boom")):
            with self.assertRaises(RuntimeError):
                self.go("Amped")
        self.assertTrue(keep.is_file())
        self.assertEqual(sorted(p.name for p in self.sauna.iterdir()), ["keep.flac"])

    def test_dry_run_and_uncued_and_running(self):
        before = self.db.read_bytes()
        res = self.go("Amped", dry_run=True)
        self.assertTrue(res.dry_run)
        self.assertFalse(self.sauna.exists())
        self.assertEqual(self.db.read_bytes(), before)
        with patch.object(R, "is_virtualdj_running", return_value=True):
            with self.assertRaises(RuntimeError):
                self.go("Amped")
        self.assertFalse(self.sauna.exists())
        self.db.write_bytes(db_text(self.src, uncued=True))
        with self.assertRaises(PermissionError):
            self.go("Amped")

    def test_set_folder_override_only_for_zz_test_names(self):
        with self.assertRaises(ValueError):
            R.sauna_fest_set_dir("Goth")
        self.assertEqual(R.sauna_fest_set_dir("ZZ TEST x"), self.sets / "ZZ TEST x")
        self.assertEqual(R.sauna_fest_set_dir(None), self.sauna)
        self.assertEqual(_config.SAUNA_FEST_SET_NAME, "Sauna Fest")

    def test_new_house_folder_does_not_count_for_the_set_folder(self):
        from sorter import house_folders as hf

        self.go("Sunrise", dest_extra={"new_folder": True})
        self.assertEqual(hf.new_folder_state()["folders"], ["Sunrise"])  # only the House one

    def test_safe_clone_songs_is_atomic_when_source_missing(self):
        before = self.db.read_bytes()
        with self.assertRaises(safe_write.SaveFailed):
            safe_write.safe_clone_songs(self.db, "/nope.flac", [("/a.flac", None), ("/b.flac", None)])
        self.assertEqual(self.db.read_bytes(), before)


    def test_safe_remove_songs_restores_baseline_bytes(self):
        before = self.db.read_bytes()
        self.go("Amped")
        self.assertEqual(V.read_vdj_database_text(self.db).count("<Song "), 4)
        house = (self.house / "Amped" / "Song One.flac").resolve()
        sets = (self.sauna / "Amped" / "Song One.flac").resolve()
        res = safe_write.safe_remove_songs(self.db, [str(house), str(sets), "/not/there.flac"])
        self.assertEqual(len(res["removed"]), 2)
        self.assertEqual(res["missing"], ["/not/there.flac"])
        self.assertEqual(self.db.read_bytes(), before)  # byte-identical to baseline


class TagFolderSuggestTests(unittest.TestCase):
    def setUp(self) -> None:
        HouseCopySortTests.setUp(self)
        (self.house / "Chill" / "Journey").mkdir(parents=True)

    def tearDown(self) -> None:
        HouseCopySortTests.tearDown(self)

    def test_title_case_and_sanitizing(self):
        from sorter import house_folders as hf

        self.assertEqual(hf.tag_to_folder_name("deep house"), "Deep House")
        self.assertEqual(hf.tag_to_folder_name("  DOWNTEMPO   deep "), "Downtempo Deep")
        self.assertEqual(hf.tag_to_folder_name("a/b:c"), "A B C")
        self.assertEqual(hf.tag_to_folder_name("..sneaky"), "Sneaky")
        self.assertEqual(hf.tag_to_folder_name("///"), "")

    def test_new_similar_and_cap_statuses_create_nothing(self):
        from sorter import house_folders as hf

        r = hf.suggest_tag_folder("hypnotic")
        self.assertEqual((r["status"], r["name"], r["count"], r["max"]), ("new", "Hypnotic", 0, None))
        self.assertFalse((self.house / "Hypnotic").exists())
        self.assertEqual(hf.new_folder_state()["count"], 0)
        s = hf.suggest_tag_folder("journey")  # case-insensitive collision with Chill/Journey
        self.assertEqual((s["status"], s["existing"]), ("similar_exists", "Chill/Journey"))
        self.assertEqual(hf.suggest_tag_folder("Journeys")["status"], "similar_exists")
        for n in ("N1", "N2", "N3", "N4"):
            hf.claim_new_folder(n)
        c = hf.suggest_tag_folder("hypnotic")
        self.assertEqual(c["status"], "new")  # never "cap reached" any more
        self.assertEqual(hf.suggest_tag_folder("journey")["status"], "similar_exists")  # existing still usable
        self.assertFalse((self.house / "Hypnotic").exists())
        self.assertEqual(hf.suggest_tag_folder("")["status"], "invalid")


class SortedStateTests(unittest.TestCase):
    def setUp(self) -> None:
        HouseCopySortTests.setUp(self)
        self.dest = self.house / "Amped" / "Song One.flac"

    def tearDown(self) -> None:
        HouseCopySortTests.tearDown(self)

    class _T:
        def __init__(self, path):
            self.path = str(path)

    def test_sorted_row_leaves_and_stays_gone(self):
        self.dest.write_bytes(b"copy")
        tracks = [self._T(self.src)]
        self.assertEqual(len(SS.drop_sorted(tracks)), 1)
        SS.record_sorted(self.src, [self.dest])
        self.assertEqual(SS.drop_sorted(tracks), [])  # gone
        self.assertEqual(SS.drop_sorted(tracks), [])  # and stays gone (persisted file)
        self.assertTrue((self.notes / "sorted.json").is_file())

    def test_row_returns_if_copy_deleted_or_source_replaced(self):
        self.dest.write_bytes(b"copy")
        SS.record_sorted(self.src, [self.dest])
        self.dest.unlink()
        self.assertEqual(len(SS.drop_sorted([self._T(self.src)])), 1)
        self.dest.write_bytes(b"copy")
        self.assertEqual(SS.drop_sorted([self._T(self.src)]), [])
        self.src.write_bytes(b"a brand new file with different size")
        self.assertEqual(len(SS.drop_sorted([self._T(self.src)])), 1)

    def test_backfill_from_action_log(self):
        import json
        from datetime import datetime, timedelta

        self.dest.write_bytes(b"copy")
        ts = (datetime.now().astimezone() + timedelta(seconds=5)).isoformat(timespec="seconds")
        rec = {"action": "sort", "success": True, "ts": ts, "source_path": str(self.src),
               "details": {"library_dests": [{"path": str(self.dest)}]}}
        (self.notes / "music-sorter-actions.jsonl").write_text(json.dumps(rec) + "\n")
        self.assertEqual(SS.drop_sorted([self._T(self.src)]), [])


class RecommendationDescriptorTests(unittest.TestCase):
    def test_schema_and_result_carry_description_and_tags(self):
        from sorter import recommend as RC

        sch = RC.HouseRecommendationSchema.model_validate(
            {
                "house": {"relative_path": "x", "confidence": 0.5, "reasoning": "r"},
                "description": "Lush pads over a rolling groove.",
                "vibe_tags": ["Deep House", "Dreamy", "Hypnotic"],
            }
        )
        self.assertEqual(sch.description, "Lush pads over a rolling groove.")
        res = RC.RecommendationResult(
            library="House", relative_path="x", confidence=0.5, reasoning="r",
            vibe_tags=list(sch.vibe_tags), description=sch.description,
        )
        d = res.to_dict()
        self.assertEqual(d["description"], "Lush pads over a rolling groove.")
        self.assertEqual(d["vibe_tags"], ["Deep House", "Dreamy", "Hypnotic"])
        self.assertIn("description:", RC.build_prompt("a.flac", 120.0, ["Chill"]))


class UiWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.js = (root / "static" / "app.js").read_text(encoding="utf-8")
        self.html = (root / "static" / "index.html").read_text(encoding="utf-8")
        self.app = (root / "app.py").read_text(encoding="utf-8")

    def test_sauna_toggle_default_on_and_endpoint_wired(self):
        self.assertIn('id="alsoSaunaChk" checked', self.html)
        self.assertIn("Also copy to Sets/Sauna Fest", self.html)
        self.assertIn("also_sauna_fest: Boolean(sauna)", self.js)
        self.assertIn("also_sauna_fest: bool = True", self.app)
        self.assertNotIn('id="saunaBtn"', self.html)
        self.assertIn('@app.post("/api/sort-sauna-fest")', self.app)

    def test_tag_chips_and_cap_wiring(self):
        self.assertIn("rec-tag-chips", self.js)
        self.assertIn('data-action="tag-folder"', self.js)
        self.assertIn("/api/house-folder-suggest", self.js)
        self.assertIn("New folders created: ${nf.count || 0} (no limit)", self.js)
        self.assertIn("New folder: ${escapeHtml(info.name)}", self.js)
        self.assertIn("rec.description", self.js)
        self.assertIn('@app.get("/api/house-folder-suggest")', self.app)

    def test_sorted_row_leaves_list_wiring(self):
        self.assertIn("_sorted_state.drop_sorted(add_tracks)", self.app)
        self.assertIn("_sorted_state.record_sorted", self.app)
        self.assertIn("dropSongState(track.path)", self.js)  # drops the row + every per-song leftover

    def test_pajamathon_add_button_is_gone(self):
        self.assertNotIn("Add to Pajamathon", self.js + self.html)
        self.assertNotIn("addTrackToPajamathon", self.js)
        self.assertNotIn("placement-add-set-btn", self.js)


if __name__ == "__main__":
    unittest.main()
