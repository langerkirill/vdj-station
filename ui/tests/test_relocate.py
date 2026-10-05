"""Move + VDJ FilePath relocate, including uncued gate."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import relocate as relocate_mod


def sample_db(path: str) -> bytes:
    return (
        "<VirtualDJ_Database>\r\n"
        f'<Song FilePath="{path}" Flag="1">\r\n'
        '  <Tags Author="A" Title="T" Key="Am" />\r\n'
        '  <Scan Bpm="0.5" />\r\n'
        '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
        '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
        '  <Poi Name="Loop" Pos="8.0" Num="-1" Color="1" Type="loop" Size="16.0" Slot="1" />\r\n'
        "</Song>\r\n"
        "</VirtualDJ_Database>\r\n"
    ).encode("utf-8")


def sample_db_uncued(path: str) -> bytes:
    return (
        "<VirtualDJ_Database>\r\n"
        f'<Song FilePath="{path}">\r\n'
        '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
        "</Song>\r\n"
        "</VirtualDJ_Database>\r\n"
    ).encode("utf-8")


class RelocateTests(unittest.TestCase):






    def test_summarize_cues_for_paths_reads_database_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.flac"
            b = root / "b.flac"
            a.write_bytes(b"x")
            b.write_bytes(b"y")
            db = root / "database.xml"
            db.write_text(
                "<VirtualDJ_Database>\r\n"
                f'<Song FilePath="{a.resolve()}">\r\n'
                '  <Poi Name="Intro" Pos="0.1" Num="1" Type="cue" />\r\n'
                "</Song>\r\n"
                f'<Song FilePath="{b.resolve()}">\r\n'
                '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                "</Song>\r\n"
                "</VirtualDJ_Database>\r\n",
                encoding="utf-8",
            )
            reads = {"n": 0}
            real_read = relocate_mod.read_vdj_database_text

            def counting_read(path):
                reads["n"] += 1
                return real_read(path)

            with patch.object(relocate_mod, "read_vdj_database_text", side_effect=counting_read):
                out = relocate_mod.summarize_cues_for_paths(
                    [str(a), str(b), str(root / "missing.flac")],
                    database_path=db,
                )
            self.assertEqual(reads["n"], 1)
            self.assertTrue(out[str(a)].is_cued)
            self.assertEqual(out[str(a)].cue_count, 1)
            self.assertTrue(out[str(b)].in_database)
            self.assertFalse(out[str(b)].is_cued)
            self.assertFalse(out[str(root / "missing.flac")].in_database)

    def test_summarize_is_cued(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "a.flac"
            audio.write_bytes(b"x")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(audio.resolve())))
            cues = relocate_mod.summarize_cues(audio, db)
            self.assertTrue(cues.is_cued)
            self.assertEqual(cues.cue_count, 1)
            self.assertEqual(cues.loop_count, 1)
            self.assertEqual(len(cues.points), 2)
            self.assertEqual(cues.points[0].name, "Intro")
            self.assertAlmostEqual(cues.points[0].pos, 0.1)
            self.assertEqual(cues.points[0].color_name, "blue")
            self.assertEqual(cues.points[1].kind, "loop")
            self.assertAlmostEqual(cues.bpm or 0, 120.0, places=1)
            self.assertEqual(cues.key, "Am")
            self.assertEqual(cues.camelot, "8A")
            payload = cues.to_dict()
            self.assertEqual(payload["points"][0]["name"], "Intro")
            self.assertEqual(payload["key"], "Am")
            self.assertEqual(payload["camelot"], "8A")

    def test_vdj_bpm_conversion(self):
        self.assertAlmostEqual(relocate_mod.vdj_bpm_to_actual(0.5), 120.0)
        self.assertAlmostEqual(relocate_mod.vdj_bpm_to_actual(128.0), 128.0)
        self.assertIsNone(relocate_mod.vdj_bpm_to_actual(None))

    def test_both_library_mode_is_not_available_in_house(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Ready"
            cues_sorted = root / "Cues Sorted"
            ready.mkdir()
            cues_sorted.mkdir()
            src = ready / "partial.flac"
            src.write_bytes(b"audio")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(src.resolve())))
            with patch(
                "sorter.library.LIBRARIES", {"House": cues_sorted}
            ), patch.object(
                relocate_mod, "CUES_SORTED", cues_sorted
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                with self.assertRaises((KeyError, ValueError, RuntimeError, FileNotFoundError)):
                    relocate_mod.sort_track(
                        src,
                        library_name="Both",
                        relative_folder="Amped",
                        database_path=db,
                        ready_root=ready,
                        create_backup=False,
                    )
            self.assertTrue(src.is_file())
            self.assertEqual(list(cues_sorted.iterdir()), [])

    def test_trash_failure_does_not_hard_unlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keep.flac"
            path.write_bytes(b"audio")
            with patch("sorter.relocate.subprocess.run") as run:
                run.return_value = type(
                    "R",
                    (),
                    {"returncode": 1, "stderr": "Finder busy", "stdout": ""},
                )()
                with self.assertRaises(RuntimeError):
                    relocate_mod._trash_or_unlink(path, to_trash=True)
            self.assertTrue(path.is_file())

    def test_remove_from_ready_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / "Ready"
            ready.mkdir()
            src = ready / "skip-me.flac"
            src.write_bytes(b"audio")
            stems = Path(f"{src}.vdjstems")
            stems.write_bytes(b"stems")

            result = relocate_mod.remove_from_ready_for_sort(
                src,
                ready_root=ready,
                to_trash=False,
                remove_from_database=False,
            )
            self.assertFalse(src.exists())
            self.assertFalse(stems.exists())
            self.assertEqual(result["name"], "skip-me.flac")
            self.assertEqual(len(result["removed"]), 2)

            with self.assertRaises(ValueError):
                other = Path(tmp) / "elsewhere.flac"
                other.write_bytes(b"x")
                relocate_mod.remove_from_ready_for_sort(
                    other,
                    ready_root=ready,
                    to_trash=False,
                    remove_from_database=False,
                )

    def test_assess_cue_readiness(self):
        empty = relocate_mod.CueSummary(
            cue_count=0,
            loop_count=0,
            has_beatgrid=False,
            title="",
            author="",
            in_database=False,
        )
        self.assertEqual(relocate_mod.assess_cue_readiness(empty)["status"], "missing")

        partial = relocate_mod.CueSummary(
            cue_count=1,
            loop_count=0,
            has_beatgrid=True,
            title="t",
            author="a",
            in_database=True,
        )
        self.assertEqual(relocate_mod.assess_cue_readiness(partial)["status"], "partial")

        one_loop = relocate_mod.CueSummary(
            cue_count=3,
            loop_count=1,
            has_beatgrid=True,
            title="t",
            author="a",
            in_database=True,
        )
        one = relocate_mod.assess_cue_readiness(one_loop)
        self.assertFalse(one["ready"])
        self.assertEqual(one["status"], "partial")
        self.assertIn("2 loops", one["label"].lower())

        ready = relocate_mod.CueSummary(
            cue_count=3,
            loop_count=2,
            has_beatgrid=True,
            title="t",
            author="a",
            in_database=True,
        )
        assessment = relocate_mod.assess_cue_readiness(ready)
        self.assertTrue(assessment["ready"])
        self.assertEqual(assessment["status"], "ready")

    def test_promote_add_cues_to_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add = root / "Add Cues" / "Batch"
            ready = root / "Ready For Sort"
            add.mkdir(parents=True)
            ready.mkdir()
            src = add / "track.flac"
            src.write_bytes(b"audio")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(src.resolve())))

            with patch.object(relocate_mod, "ADD_CUES", root / "Add Cues"), patch.object(
                relocate_mod, "READY_FOR_SORT", ready
            ), patch.object(
                relocate_mod,
                "CUE_STAGES",
                {
                    "ready_for_sort": ready,
                    "no_cues_found": root / "No Cues Found",
                    "ac_low_quality": root / "AC Low Quality",
                    "low_quality_skip": root / "Low Quality Skip",
                },
            ), patch.object(relocate_mod, "CUES_ROOT", root), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.promote_add_cues_track(
                    src,
                    destination_stage="ready_for_sort",
                    database_path=db,
                    create_backup=True,
                )

            dest = ready / "track.flac"
            self.assertTrue(dest.is_file())
            self.assertFalse(src.exists())
            self.assertTrue(result.database_updated)
            self.assertIn(str(dest.resolve()).encode(), db.read_bytes())

    def test_promote_rejects_pajamathon_set_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add = root / "Add Cues"
            ready = root / "Ready For Sort"
            sets = root / "Sets" / "Pajamathon 2026"
            add.mkdir()
            ready.mkdir()
            sets.mkdir(parents=True)
            src = sets / "087. Give A Little.mp3"
            src.write_bytes(b"set")
            with patch.object(relocate_mod, "ADD_CUES", add), patch(
                "sorter.library.SETS_ROOT", root / "Sets"
            ):
                with self.assertRaisesRegex(ValueError, "already in the Pajamathon set"):
                    relocate_mod.promote_add_cues_track(
                        src,
                        destination_stage="ready_for_sort",
                        dry_run=True,
                    )

    def test_demote_ready_to_add_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add = root / "Add Cues"
            ready = root / "Ready For Sort"
            add.mkdir(parents=True)
            ready.mkdir()
            src = ready / "track.flac"
            src.write_bytes(b"audio")
            stems = Path(f"{src}.vdjstems")
            stems.write_bytes(b"stems")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(src.resolve())))

            with patch.object(relocate_mod, "ADD_CUES", add), patch.object(
                relocate_mod, "READY_FOR_SORT", ready
            ), patch.object(relocate_mod, "CUES_ROOT", root), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.demote_ready_to_add_cues(
                    src,
                    database_path=db,
                    create_backup=False,
                    subfolder="Back from Ready",
                )

            dest = add / "Back from Ready" / "track.flac"
            self.assertTrue(dest.is_file())
            self.assertFalse(src.exists())
            self.assertTrue(Path(f"{dest}.vdjstems").is_file())
            self.assertEqual(Path(result.dest_path).resolve(), dest.resolve())
            self.assertTrue(result.database_updated)
            self.assertIn(str(dest.resolve()).encode(), db.read_bytes())

    def test_delete_library_placement_removes_file_and_vdj_song(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zouk = root / "Zouk" / "Chill"
            zouk.mkdir(parents=True)
            audio = zouk / "dup.flac"
            audio.write_bytes(b"audio")
            stems = Path(f"{audio}.vdjstems")
            stems.write_bytes(b"stems")
            other = root / "other.flac"
            other.write_bytes(b"keep")
            db = root / "database.xml"
            # Two songs: placement + unrelated keep.
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{audio.resolve()}" Flag="1">\r\n'
                    '  <Tags Author="A" Title="T" />\r\n'
                    '  <Scan Bpm="0.5" />\r\n'
                    '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
                    '  <Poi Name="Loop" Pos="8.0" Num="-1" Color="1" Type="loop" Size="16.0" Slot="1" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{other.resolve()}">\r\n'
                    '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                    '  <Poi Name="Keep" Pos="1.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )

            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.delete_library_placement(
                    audio,
                    database_path=db,
                    to_trash=False,
                    create_backup=True,
                )

            self.assertTrue(result["ok"])
            self.assertFalse(audio.exists())
            self.assertFalse(stems.exists())
            self.assertTrue(other.exists())
            text = db.read_text(encoding="utf-8")
            self.assertNotIn(str(audio.resolve()), text)
            self.assertIn(str(other.resolve()), text)
            self.assertIn('Name="Keep"', text)
            self.assertNotIn('Name="Intro"', text)
            self.assertTrue(result["database"]["removed_from_db"])
            self.assertEqual(result["had_cues"], 1)
            self.assertEqual(result["had_loops"], 1)

            # Safety: refuse Ready for Sort / random paths.
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk"}
            ), patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"):
                with self.assertRaises(ValueError):
                    relocate_mod.delete_library_placement(
                        other, database_path=db, to_trash=False
                    )

    def test_delete_pajamathon_set_placement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Sets" / "Pajamathon 2026"
            paj.mkdir(parents=True)
            audio = paj / "407. 01 Dusk Till Dawn - Kizomba Remix.m4a"
            audio.write_bytes(b"set-audio")
            stems = Path(f"{audio}.vdjstems")
            stems.write_bytes(b"stems")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(audio.resolve())))
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.delete_library_placement(
                    audio,
                    database_path=db,
                    to_trash=False,
                    create_backup=False,
                )
            self.assertTrue(result["ok"])
            self.assertFalse(audio.exists())
            self.assertFalse(stems.exists())
            self.assertEqual(result["root_name"], "Pajamathon 2026")
            self.assertIn("Pajamathon 2026/", result["relative_path"])
            self.assertNotIn(str(audio.resolve()), db.read_text(encoding="utf-8"))

    def test_delete_library_placement_keeps_set_hardlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zouk = root / "Zouk" / "Energy"
            paj = root / "Sets" / "Pajamathon 2026"
            zouk.mkdir(parents=True)
            paj.mkdir(parents=True)
            lib = (zouk / "Ash and Naila - Give A Little.mp3").resolve()
            lib.write_bytes(b"shared")
            set_copy = (paj / "087. Give A Little.mp3").resolve()
            os.link(lib, set_copy)
            db = root / "database.xml"
            db.write_bytes(sample_db(str(lib)))
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.delete_library_placement(
                    lib,
                    database_path=db,
                    to_trash=True,
                    create_backup=False,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result["unlink_only"])
            self.assertFalse(lib.exists())
            self.assertTrue(set_copy.is_file())
            self.assertEqual(set_copy.read_bytes(), b"shared")

    def test_delete_missing_pajamathon_placement_still_removes_vdj_song(self):
        """Already-trashed set copies must still drop their VirtualDJ Song."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Sets" / "Pajamathon 2026"
            paj.mkdir(parents=True)
            audio = paj / "407. 01 Dusk Till Dawn - Kizomba Remix.m4a"
            audio.write_bytes(b"set-audio")
            stems = Path(f"{audio}.vdjstems")
            stems.write_bytes(b"stems")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(audio.resolve())))
            audio.unlink()
            self.assertFalse(audio.exists())
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.delete_library_placement(
                    audio,
                    database_path=db,
                    to_trash=False,
                    create_backup=False,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result.get("missing_file"))
            self.assertFalse(stems.exists())
            self.assertTrue(result["database"]["removed_from_db"])
            self.assertNotIn(str(audio.resolve()), db.read_text(encoding="utf-8"))

    def test_delete_missing_placement_ok_when_not_in_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Sets" / "Pajamathon 2026"
            paj.mkdir(parents=True)
            audio = paj / "090. Vlad Ivan - Dusk Till Dawn - Kizomba Remix.m4a"
            db = root / "database.xml"
            db.write_bytes(sample_db(str((paj / "other.m4a").resolve())))
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.delete_library_placement(
                    audio,
                    database_path=db,
                    to_trash=False,
                    create_backup=False,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result.get("missing_file"))
            self.assertEqual(result["database"].get("reason"), "not_in_database")

    def test_delete_missing_ghost_ok_when_vdj_open_and_not_in_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Sets" / "Pajamathon 2026"
            paj.mkdir(parents=True)
            audio = paj / "407. 01 Dusk Till Dawn - Kizomba Remix.m4a"
            db = root / "database.xml"
            db.write_bytes(sample_db(str((paj / "other.m4a").resolve())))
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=True
            ):
                result = relocate_mod.delete_library_placement(
                    audio,
                    database_path=db,
                    to_trash=False,
                    create_backup=False,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result.get("missing_file"))
            self.assertEqual(result["database"].get("reason"), "not_in_database")


def _copy_cues_db(source_path: str, dest_path: str, *, dest_cued: bool = False) -> bytes:
    dest_cues = (
        '  <Poi Name="Old Cue" Pos="1.0" Num="1" Color="4294967040" Type="cue" />\r\n'
        if dest_cued
        else ""
    )
    return (
        "<VirtualDJ_Database>\r\n"
        f'<Song FilePath="{source_path}" Flag="1">\r\n'
        '  <Tags Author="A" Title="T" />\r\n'
        '  <Scan Bpm="0.5" />\r\n'
        '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
        '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
        '  <Poi Name="Loop" Pos="8.0" Num="-1" Color="1" Type="loop" Size="16.0" Slot="1" />\r\n'
        "</Song>\r\n"
        f'<Song FilePath="{dest_path}">\r\n'
        '  <Tags Author="A" Title="T" User2="RnB" />\r\n'
        '  <Scan Bpm="0.465" />\r\n'
        '  <Poi Pos="0.2" Type="beatgrid" />\r\n'
        f"{dest_cues}"
        "  <Comment>keep-me</Comment>\r\n"
        "</Song>\r\n"
        "</VirtualDJ_Database>\r\n"
    ).encode("utf-8")


class CopyCuesToPlacementTests(unittest.TestCase):
    def _setup(self, tmp: str, *, dest_cued: bool = False, dest_in_db: bool = True):
        root = Path(tmp)
        ready = root / "Ready For Sort"
        zouk = root / "Zouk" / "RnB"
        ready.mkdir(parents=True)
        zouk.mkdir(parents=True)
        src = ready / "Moon.flac"
        dest = zouk / "01 - Amaria - Moon.flac"
        src.write_bytes(b"ready-audio")
        dest.write_bytes(b"library-audio")
        db = root / "database.xml"
        if dest_in_db:
            db.write_bytes(_copy_cues_db(str(src.resolve()), str(dest.resolve()), dest_cued=dest_cued))
        else:
            db.write_bytes(sample_db(str(src.resolve())))
        patches = [
            patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ),
            patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"),
            patch.object(relocate_mod, "READY_FOR_SORT", ready),
            patch.object(relocate_mod, "ADD_CUES", root / "Add Cues"),
            patch.object(relocate_mod, "VDJ_DATABASE", db),
            patch("sorter.relocate.is_virtualdj_running", return_value=False),
            patch("vdj_database_safety.is_virtualdj_running", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return src, dest, db

    def test_injects_cues_into_existing_uncued_library_song(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            result = relocate_mod.copy_cues_to_placement(
                src, dest, database_path=db, create_backup=True
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "injected")
            self.assertEqual(result["copied_cues"], 1)
            self.assertEqual(result["copied_loops"], 1)
            self.assertTrue(src.is_file(), "Ready for Sort file must stay")
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), b"library-audio")

            raw = db.read_bytes()
            self.assertIn(b"\r\n", raw)
            text = raw.decode("utf-8")
            dest_span_start = text.index(str(dest.resolve()))
            dest_block = text[dest_span_start : text.index("</Song>", dest_span_start)]
            self.assertIn('Name="Intro"', dest_block)
            self.assertIn('Type="loop"', dest_block)
            self.assertIn('Pos="0.2"', dest_block)
            self.assertIn('Bpm="0.465"', dest_block)
            self.assertIn("keep-me", dest_block)
            self.assertIn("User2=", dest_block)
            self.assertNotIn('Name="Old Cue"', dest_block)
            self.assertIn(str(src.resolve()), text)

    def test_inject_fills_blank_directory_sort_and_title_color(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Ready For Sort"
            zouk = root / "Zouk" / "Chill" / "Deep"
            sets = root / "Sets" / "Pajamathon 2026"
            ready.mkdir(parents=True)
            zouk.mkdir(parents=True)
            sets.mkdir(parents=True)
            src = zouk / "117. Hold Me.wav"
            dest = sets / "448. Hold Me.wav"
            src.write_bytes(b"a")
            dest.write_bytes(b"b")
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}">\r\n'
                    '  <Tags Author="A" Title="T" User2="Chill/Deep" />\r\n'
                    '  <Infos SongLength="10" UserColor="4278190335" />\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest.resolve()}">\r\n'
                    '  <Tags Author="A" Title="T" />\r\n'
                    '  <Infos SongLength="10" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"), patch.object(
                relocate_mod, "READY_FOR_SORT", ready
            ), patch.object(
                relocate_mod, "ADD_CUES", root / "Add Cues"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, create_backup=False
                )
            self.assertTrue(result["ok"])
            text = db.read_text(encoding="utf-8")
            dest_block = text[text.index(str(dest.resolve())) : text.index("</Song>", text.index(str(dest.resolve())))]
            self.assertIn('User2="Chill/Deep"', dest_block)
            self.assertIn('UserColor="4278190335"', dest_block)
            self.assertIn('Name="Intro"', dest_block)

    def test_clones_song_when_dest_missing_from_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp, dest_in_db=False)
            result = relocate_mod.copy_cues_to_placement(
                src, dest, database_path=db, create_backup=False
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "cloned")
            text = db.read_text(encoding="utf-8")
            self.assertIn(dest.name, text)
            self.assertIn(str(src.resolve()), text)
            self.assertIn('Name="Intro"', text)
            dest_span = text.index(dest.name)
            dest_block = text[dest_span : text.index("</Song>", dest_span)]
            self.assertIn('Name="Intro"', dest_block)

    def test_refuses_overwrite_of_loop_only_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}" Flag="1">\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest.resolve()}">\r\n'
                    '  <Poi Pos="0.2" Type="beatgrid" />\r\n'
                    '  <Poi Name="Loop" Pos="8.0" Num="-1" Type="loop" Size="16.0" Slot="1" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with self.assertRaises(ValueError) as ctx:
                relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, overwrite=False, create_backup=False
                )
            self.assertIn("already has", str(ctx.exception).lower())

    def test_refuses_overwrite_without_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp, dest_cued=True)
            with self.assertRaises(ValueError) as ctx:
                relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, overwrite=False, create_backup=False
                )
            self.assertIn("already has", str(ctx.exception).lower())
            text = db.read_text(encoding="utf-8")
            self.assertIn('Name="Old Cue"', text)
            self.assertEqual(text.count('Name="Intro"'), 1)

    def test_overwrite_replaces_dest_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp, dest_cued=True)
            result = relocate_mod.copy_cues_to_placement(
                src,
                dest,
                database_path=db,
                overwrite=True,
                create_backup=False,
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["overwrote"])
            text = db.read_text(encoding="utf-8")
            dest_span = text.index(str(dest.resolve()))
            dest_block = text[dest_span : text.index("</Song>", dest_span)]
            self.assertIn('Name="Intro"', dest_block)
            self.assertNotIn('Name="Old Cue"', dest_block)
            self.assertIn("keep-me", dest_block)

    def test_requires_source_cued(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}">\r\n'
                    '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest.resolve()}">\r\n'
                    '  <Poi Pos="0.2" Type="beatgrid" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with self.assertRaises(ValueError) as ctx:
                relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, create_backup=False
                )
            self.assertIn("cue", str(ctx.exception).lower())

    def test_refuses_non_library_dest_and_non_queue_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            other = Path(tmp) / "elsewhere.flac"
            other.write_bytes(b"x")
            with self.assertRaises(ValueError):
                relocate_mod.copy_cues_to_placement(
                    src, other, database_path=db, create_backup=False
                )
            with self.assertRaises(ValueError):
                relocate_mod.copy_cues_to_placement(
                    dest, dest, database_path=db, create_backup=False
                )

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            before = db.read_bytes()
            result = relocate_mod.copy_cues_to_placement(
                src, dest, database_path=db, dry_run=True, create_backup=False
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["mode"], "injected")
            self.assertEqual(db.read_bytes(), before)

    def test_allows_pajamathon_set_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Ready For Sort"
            paj = root / "Sets" / "Pajamathon 2026"
            ready.mkdir(parents=True)
            paj.mkdir(parents=True)
            src = ready / "01 - Amaria - Moon.flac"
            dest = paj / "140. Amaria - Moon.flac"
            src.write_bytes(b"ready")
            dest.write_bytes(b"set")
            db = root / "database.xml"
            db.write_bytes(_copy_cues_db(str(src.resolve()), str(dest.resolve())))
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"), patch.object(
                relocate_mod, "READY_FOR_SORT", ready
            ), patch.object(relocate_mod, "ADD_CUES", root / "Add Cues"), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(relocate_mod, "VDJ_DATABASE", db), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, create_backup=False
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "cloned")
            self.assertEqual(result["root_name"], "Pajamathon 2026")
            dest_block = db.read_text(encoding="utf-8")
            dest_at = dest_block.index(str(dest.resolve()))
            dest_song = dest_block[dest_at : dest_block.index("</Song>", dest_at)]
            self.assertIn('Name="Intro"', dest_song)
            self.assertIn('Type="beatgrid"', dest_song)
            self.assertIn('Type="loop"', dest_song)

    def test_allows_add_cues_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add = root / "Add Cues"
            zouk = root / "Zouk" / "Chill"
            add.mkdir(parents=True)
            zouk.mkdir(parents=True)
            src = add / "track.flac"
            dest = zouk / "track.flac"
            src.write_bytes(b"a")
            dest.write_bytes(b"b")
            db = root / "database.xml"
            db.write_bytes(_copy_cues_db(str(src.resolve()), str(dest.resolve())))
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"), patch.object(
                relocate_mod, "READY_FOR_SORT", root / "Ready For Sort"
            ), patch.object(relocate_mod, "ADD_CUES", add), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch("sorter.relocate.is_virtualdj_running", return_value=False):
                result = relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, create_backup=False
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "injected")

    def test_copy_cues_to_all_library_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            house = Path(tmp) / "House" / "Chill"
            house.mkdir(parents=True)
            dest2 = house / dest.name
            dest2.write_bytes(b"house-audio")
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}" Flag="1">\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
                    '  <Poi Name="Loop" Pos="8.0" Num="-1" Type="loop" Size="16.0" Slot="1" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest.resolve()}">\r\n'
                    '  <Poi Pos="0.2" Type="beatgrid" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest2.resolve()}">\r\n'
                    '  <Poi Pos="0.3" Type="beatgrid" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            result = relocate_mod.copy_cues_to_placements(
                src, [dest, dest2, dest], database_path=db, create_backup=False
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["copied"], 2)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["skipped"], 0)
            text = db.read_bytes().decode("utf-8")
            for path in (dest, dest2):
                start = text.index(str(path.resolve()))
                block = text[start : text.index("</Song>", start)]
                self.assertIn('Name="Intro"', block)
                self.assertIn('Type="loop"', block)

    def test_add_track_to_pajamathon_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            sets = Path(tmp) / "Sets"
            paj = sets / "Pajamathon 2026"
            paj.mkdir(parents=True)
            (paj / "083. Other.flac").write_bytes(b"x")
            with patch.object(relocate_mod, "SETS_ROOT", sets), patch(
                "sorter.library.SETS_ROOT", sets
            ):
                result = relocate_mod.add_track_to_event_set(
                    src, sets_root=sets, database_path=db, create_backup=False
                )
            added = Path(result["dest_path"])
            self.assertTrue(added.is_file())
            self.assertEqual(added.parent.resolve(), paj.resolve())
            self.assertTrue(added.name.startswith("084. "))
            self.assertEqual(added.read_bytes(), b"ready-audio")
            self.assertIn(str(added.resolve()).encode(), db.read_bytes())
            self.assertIn(b'Name="Intro"', db.read_bytes())
            self.assertTrue(src.is_file())

            with patch.object(relocate_mod, "SETS_ROOT", sets), patch(
                "sorter.library.SETS_ROOT", sets
            ):
                again = relocate_mod.add_track_to_event_set(
                    src, sets_root=sets, database_path=db, create_backup=False
                )
            self.assertTrue(again["already_exists"])
            self.assertEqual(Path(again["dest_path"]).resolve(), added.resolve())
            self.assertEqual(again["relative_path"], f"{paj.name}/{added.name}")

    def test_add_to_set_from_ready_uses_zouk_directory_sort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Cues" / "Ready For Sort"
            zouk = root / "Zouk" / "Neo Zouk"
            sets = root / "Sets" / "Pajamathon 2026"
            ready.mkdir(parents=True)
            zouk.mkdir(parents=True)
            sets.mkdir(parents=True)
            src = ready / "Linker - Magic Garden.mp3"
            lib = zouk / "Linker - Magic Garden.mp3"
            src.write_bytes(b"ready")
            lib.write_bytes(b"lib")
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}">\r\n'
                    '  <Tags Author="Linker" Title="Magic Garden" User2="Neo Zouk" />\r\n'
                    '  <Infos SongLength="10" UserColor="4294902015" />\r\n'
                    '  <Scan Bpm="0.750" Phase="61.8" />\r\n'
                    '  <Poi Pos="61.8" Type="beatgrid" />\r\n'
                    '  <Poi Name="Beat Entry" Pos="61.8" Num="1" Color="4278255360" Type="cue" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch(
                "sorter.library.LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "READY_FOR_SORT", ready
            ), patch.object(
                relocate_mod, "ADD_CUES", root / "Add Cues"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch(
                "sorter.library.SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.add_track_to_event_set(
                    src, sets_root=root / "Sets", database_path=db, create_backup=False
                )
            dest = Path(result["dest_path"])
            text = db.read_text(encoding="utf-8")
            dest_at = text.index(str(dest.resolve()))
            dest_block = text[dest_at : text.index("</Song>", dest_at)]
            src_at = text.index(str(src.resolve()))
            src_block = text[src_at : text.index("</Song>", src_at)]
            self.assertIn('User2="Neo Zouk"', dest_block)
            self.assertNotIn("Ready For Sort", dest_block.split("<Tags", 1)[-1])
            self.assertIn('Name="Beat Entry"', dest_block)
            self.assertIn('Name="Beat Entry"', src_block)
            self.assertIn(str(src.resolve()), src_block)

    def test_add_to_set_retries_clone_when_set_copy_is_thin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zouk = root / "Zouk" / "Meridyun"
            sets = root / "Sets" / "Pajamathon 2026"
            zouk.mkdir(parents=True)
            sets.mkdir(parents=True)
            src = zouk / "Slipping Through.wav"
            dest = sets / "462. Slipping Through.wav"
            src.write_bytes(b"src")
            dest.write_bytes(b"dest")
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}">\r\n'
                    '  <Tags Author="A" Title="Slipping" User2="Meridyun" />\r\n'
                    '  <Infos SongLength="10" UserColor="4278255360" />\r\n'
                    '  <Scan Bpm="0.75" Phase="0.6" />\r\n'
                    '  <Poi Pos="0.6" Type="beatgrid" />\r\n'
                    '  <Poi Name="Beat Entry" Pos="0.6" Num="1" Color="4278255360" Type="cue" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest.resolve()}">\r\n'
                    '  <Tags Author="A" Title="Slipping" TrackNumber="462" />\r\n'
                    '  <Infos SongLength="10" UserColor="1" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch(
                "sorter.library.LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "READY_FOR_SORT", root / "Ready For Sort"
            ), patch.object(
                relocate_mod, "ADD_CUES", root / "Add Cues"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch(
                "sorter.library.SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.add_track_to_event_set(
                    src, sets_root=root / "Sets", database_path=db, create_backup=False
                )
            self.assertTrue(result["already_exists"])
            self.assertGreaterEqual(result["copied_cues"], 1)
            text = db.read_text(encoding="utf-8")
            dest_at = text.index(str(dest.resolve()))
            dest_block = text[dest_at : text.index("</Song>", dest_at)]
            self.assertIn('Name="Beat Entry"', dest_block)
            self.assertIn('User2="Meridyun"', dest_block)
            self.assertIn('UserColor="4278255360"', dest_block)
            self.assertIn(str(src.resolve()), text)

    def test_inject_replaces_placeholder_usercolor_on_library_dest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Ready For Sort"
            zouk = root / "Zouk" / "Chill" / "Deep"
            ready.mkdir(parents=True)
            zouk.mkdir(parents=True)
            src = ready / "Moon.flac"
            dest = zouk / "Moon.flac"
            src.write_bytes(b"ready")
            dest.write_bytes(b"lib")
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}">\r\n'
                    '  <Tags Author="A" Title="Moon" User2="Chill/Deep" />\r\n'
                    '  <Infos SongLength="10" UserColor="4278190335" />\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest.resolve()}">\r\n'
                    '  <Tags Author="A" Title="Moon" User2="Pajamathon 2026" />\r\n'
                    '  <Infos SongLength="10" UserColor="1" />\r\n'
                    '  <Scan Bpm="0.5" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"), patch.object(
                relocate_mod, "READY_FOR_SORT", ready
            ), patch.object(
                relocate_mod, "ADD_CUES", root / "Add Cues"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, create_backup=False
                )
            self.assertEqual(result["mode"], "injected")
            text = db.read_text(encoding="utf-8")
            dest_at = text.index(str(dest.resolve()))
            dest_block = text[dest_at : text.index("</Song>", dest_at)]
            self.assertIn('Name="Intro"', dest_block)
            self.assertIn('User2="Chill/Deep"', dest_block)
            self.assertNotIn('User2="Pajamathon 2026"', dest_block)
            self.assertIn('UserColor="4278190335"', dest_block)
            self.assertNotIn('UserColor="1"', dest_block)
            self.assertIn('Bpm="0.5"', dest_block)

    def test_add_to_set_clones_cues_colors_grid_and_directory_sort(self) -> None:
        """Sets copies must show source cues, cue colors, title color, and User2 in VDJ."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zouk = root / "Zouk" / "Neo Zouk"
            sets = root / "Sets" / "Pajamathon 2026"
            zouk.mkdir(parents=True)
            sets.mkdir(parents=True)
            src = zouk / "Linker - Magic Garden (NeoZouk) - 8744.mp3"
            src.write_bytes(b"audio")
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}">\r\n'
                    '  <Tags Author="Linker" Title="Magic Garden" User2="Neo Zouk" />\r\n'
                    '  <Infos SongLength="10" UserColor="4294902015" />\r\n'
                    '  <Scan Bpm="0.750" Phase="61.803764" />\r\n'
                    '  <Poi Pos="61.803764" Type="beatgrid" />\r\n'
                    '  <Poi Name="Beat Entry" Pos="61.803764" Num="1" Color="4278255360" Type="cue" />\r\n'
                    '  <Poi Name="Voice" Pos="97.803764" Num="2" Color="4294967040" Type="cue" />\r\n'
                    '  <Poi Name="Groove Loop" Pos="61.803764" Num="-1" Color="4278255360" Type="loop" Size="8.0" Slot="1" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"), patch.object(
                relocate_mod, "READY_FOR_SORT", root / "Ready For Sort"
            ), patch.object(
                relocate_mod, "ADD_CUES", root / "Add Cues"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch(
                "sorter.library.SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.add_track_to_event_set(
                    src, sets_root=root / "Sets", database_path=db, create_backup=False
                )
            dest = Path(result["dest_path"])
            self.assertTrue(dest.is_file())
            self.assertEqual(result["copied_cues"], 2)
            self.assertEqual(result["copied_loops"], 1)
            text = db.read_text(encoding="utf-8")
            dest_at = text.index(str(dest.resolve()))
            dest_block = text[dest_at : text.index("</Song>", dest_at)]
            self.assertIn('Name="Beat Entry"', dest_block)
            self.assertIn('Color="4278255360"', dest_block)
            self.assertIn('Color="4294967040"', dest_block)
            self.assertIn('Type="loop"', dest_block)
            self.assertIn('Type="beatgrid"', dest_block)
            self.assertIn('Phase="61.803764"', dest_block)
            self.assertIn('User2="Neo Zouk"', dest_block)
            self.assertIn('UserColor="4294902015"', dest_block)
            self.assertNotIn('User2="Pajamathon 2026"', dest_block)

    def test_copy_cues_replaces_thin_set_scan_with_source_song(self) -> None:
        """VDJ-scanned set copies keep event User2 and a wrong grid; replace the Song."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zouk = root / "Zouk" / "Neo Zouk"
            sets = root / "Sets" / "Pajamathon 2026"
            zouk.mkdir(parents=True)
            sets.mkdir(parents=True)
            src = zouk / "Linker - Magic Garden.mp3"
            dest = sets / "465. Linker - Magic Garden.mp3"
            src.write_bytes(b"a")
            dest.write_bytes(b"b")
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}">\r\n'
                    '  <Tags Author="Linker" Title="Magic Garden" User2="Neo Zouk" />\r\n'
                    '  <Infos SongLength="10" UserColor="4294902015" />\r\n'
                    '  <Scan Bpm="0.750" Phase="61.803764" />\r\n'
                    '  <Poi Pos="61.803764" Type="beatgrid" />\r\n'
                    '  <Poi Name="Beat Entry" Pos="61.803764" Num="1" Color="4278255360" Type="cue" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest.resolve()}" Flag="33554432">\r\n'
                    '  <Tags Author="Linker" Title="Magic Garden" TrackNumber="465" User2="Pajamathon 2026" />\r\n'
                    '  <Infos SongLength="10" UserColor="1" />\r\n'
                    '  <Scan Bpm="0.749977" Phase="59.932289" />\r\n'
                    '  <Poi Type="automix" Point="realStart" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"), patch.object(
                relocate_mod, "READY_FOR_SORT", root / "Ready For Sort"
            ), patch.object(
                relocate_mod, "ADD_CUES", root / "Add Cues"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, create_backup=False, overwrite=True
                )
            self.assertTrue(result["ok"])
            text = db.read_text(encoding="utf-8")
            dest_at = text.index(str(dest.resolve()))
            dest_block = text[dest_at : text.index("</Song>", dest_at)]
            self.assertIn('Name="Beat Entry"', dest_block)
            self.assertIn('Color="4278255360"', dest_block)
            self.assertIn('Type="beatgrid"', dest_block)
            self.assertIn('Phase="61.803764"', dest_block)
            self.assertIn('User2="Neo Zouk"', dest_block)
            self.assertNotIn('User2="Pajamathon 2026"', dest_block)
            self.assertIn('UserColor="4294902015"', dest_block)
            self.assertNotIn('UserColor="1"', dest_block)
            self.assertNotIn('Phase="59.932289"', dest_block)

    def test_add_to_set_copies_directory_sort_without_cues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zouk = root / "Zouk" / "Chill" / "Deep"
            sets = root / "Sets" / "Pajamathon 2026"
            zouk.mkdir(parents=True)
            sets.mkdir(parents=True)
            src = zouk / "Moon.flac"
            src.write_bytes(b"audio")
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}">\r\n'
                    '  <Tags Author="A" Title="Moon" User2="Chill/Deep" />\r\n'
                    '  <Infos SongLength="10" UserColor="4278190335" />\r\n'
                    '  <Scan Bpm="0.5" Phase="0.1" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"), patch.object(
                relocate_mod, "READY_FOR_SORT", root / "Ready For Sort"
            ), patch.object(
                relocate_mod, "ADD_CUES", root / "Add Cues"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch(
                "sorter.library.SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.add_track_to_event_set(
                    src, sets_root=root / "Sets", database_path=db, create_backup=False
                )
            dest = Path(result["dest_path"])
            text = db.read_text(encoding="utf-8")
            dest_at = text.index(str(dest.resolve()))
            dest_block = text[dest_at : text.index("</Song>", dest_at)]
            self.assertIn('User2="Chill/Deep"', dest_block)
            self.assertIn('UserColor="4278190335"', dest_block)

    def test_add_track_refuses_parenthetical_and_version_set_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / "Ready For Sort"
            ready.mkdir()
            chantaje = ready / "14 - Dj Kakah - Chantaje (Kizomba Remix).mp3"
            tunnel = ready / "Dj Kakah - Tunnel Vision 2.mp3"
            chantaje.write_bytes(b"chantaje")
            tunnel.write_bytes(b"tunnel")
            sets = Path(tmp) / "Sets"
            paj = sets / "Pajamathon 2026"
            paj.mkdir(parents=True)
            (paj / "165. Dj Kakah - Chantaje (Shakira & Maluma).mp3").write_bytes(
                b"set-chantaje"
            )
            (paj / "385. Dj Kakah - Tunnel Vision Version 2.mp3").write_bytes(
                b"set-tunnel"
            )
            with patch.object(relocate_mod, "SETS_ROOT", sets), patch(
                "sorter.library.SETS_ROOT", sets
            ), patch.object(relocate_mod, "READY_FOR_SORT", ready):
                chantaje_hit = relocate_mod.add_track_to_event_set(
                    chantaje, sets_root=sets, create_backup=False
                )
                tunnel_hit = relocate_mod.add_track_to_event_set(
                    tunnel, sets_root=sets, create_backup=False
                )
            self.assertTrue(chantaje_hit["already_exists"])
            self.assertIn("Chantaje (Shakira & Maluma)", chantaje_hit["relative_path"])
            self.assertTrue(tunnel_hit["already_exists"])
            self.assertIn("Tunnel Vision Version 2", tunnel_hit["relative_path"])

    def test_set_copy_basename_strips_space_padded_track_number(self):
        name = relocate_mod._set_copy_basename(
            Path("/ready/01 Dusk Till Dawn - Kizomba Remix.m4a")
        )
        self.assertEqual(name, "Dusk Till Dawn - Kizomba Remix.m4a")

    def test_best_cued_source_picks_library_copy_with_most_markers(self):
        from types import SimpleNamespace

        dest = Path("/Sets/Pajamathon 2026/450. Memories.wav")
        weak = Path("/Cues Sorted/Memories.wav")
        strong = Path("/Zouk/Meridyun/124. Memories.wav")

        def fake_sorted(name, index=None):
            return [{"path": str(weak)}]

        def fake_library(name, index=None):
            return [{"path": str(strong)}]

        def fake_summarize(path, database_path=None):
            p = Path(path)
            if p == strong:
                return SimpleNamespace(cue_count=6, loop_count=2)
            if p == weak:
                return SimpleNamespace(cue_count=0, loop_count=0)
            return SimpleNamespace(cue_count=0, loop_count=0)

        with patch.object(
            relocate_mod, "find_cues_sorted_matches", fake_sorted
        ), patch.object(
            relocate_mod, "find_library_matches", fake_library
        ), patch.object(
            relocate_mod, "summarize_cues", fake_summarize
        ):
            picked = relocate_mod.best_cued_source_for_set_track(dest)
        self.assertEqual(picked, strong)

    def test_copy_cues_onto_uncued_set_skips_when_already_cued(self):
        from types import SimpleNamespace

        dest = Path("/Sets/Pajamathon 2026/444. 01 Nha Rei.m4a")
        with patch.object(
            relocate_mod, "_assert_under_copy_cue_dests", return_value=dest
        ), patch.object(
            relocate_mod,
            "summarize_cues",
            return_value=SimpleNamespace(cue_count=5, loop_count=1),
        ):
            result = relocate_mod.copy_cues_onto_uncued_set_track(dest)
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "already_cued")


if __name__ == "__main__":
    unittest.main()
