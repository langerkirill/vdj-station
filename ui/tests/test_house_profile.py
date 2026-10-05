"""HOUSE FORK: profile, read-only guards, fs guard, BPM/Camelot UI helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vdj_database_safety as safety
from sorter import profile

try:
    from tests.js_assets import UI_STATIC
except ImportError:  # pragma: no cover
    from js_assets import UI_STATIC

UI_DIR = Path(__file__).resolve().parents[1]
ROOT = UI_DIR.parent


class ProfileTests(unittest.TestCase):
    def test_house_folder_list_is_the_real_house_subfolders(self) -> None:
        from sorter import house_folders

        with tempfile.TemporaryDirectory() as tmp:
            house = Path(tmp) / "House"
            for d in ("Amped", "Bassy", "Chill/Journey", "Energy/Light", ".hidden", "Amped/low_quality_backups"):
                (house / d).mkdir(parents=True)
            with patch.object(house_folders, "house_root", lambda: house):
                self.assertEqual(
                    profile.house_sort_folders(),
                    ["Amped", "Bassy", "Chill", "Chill/Journey", "Energy", "Energy/Light"],
                )
        self.assertFalse(any(n.startswith("OH ") for n in profile.house_sort_folders()))

    def test_defaults(self) -> None:
        self.assertTrue(profile.IS_HOUSE)
        self.assertEqual(profile.DEFAULT_ADD_CUES_CRATE, "Sauna Fest House")
        self.assertEqual((profile.TARGET_BPM, profile.TARGET_BPM_MIN, profile.TARGET_BPM_MAX), (120, 115, 125))
        self.assertEqual(profile.GEMINI_MODEL, "gemini-3.8-flash")

    def test_readonly_is_read_live(self) -> None:
        with patch.dict(os.environ, {"MUSIC_SORTER_READONLY": "1"}):
            self.assertTrue(profile.readonly())
        with patch.dict(os.environ, {"MUSIC_SORTER_READONLY": "0"}):
            self.assertFalse(profile.readonly())

    def test_folders_are_not_created_by_importing_config(self) -> None:
        from sorter.config import HOUSE_ROOT

        for name in profile.house_sort_folders():
            self.assertTrue((HOUSE_ROOT / name).is_dir())  # listing only returns real folders


class HouseRootTests(unittest.TestCase):
    """Sort destinations are the EXISTING House subfolders; House is a cued destination."""

    def test_house_library_is_the_existing_house_folder(self) -> None:
        from sorter import config

        self.assertEqual(config.HOUSE_ROOT, config.MUSIC_ROOT / "House")
        self.assertEqual(config.LIBRARIES, {"House": config.HOUSE_ROOT})
        self.assertNotIn("Cues Sorted House", str(config.HOUSE_ROOT))
        self.assertEqual(
            config.CUED_DESTINATION_ROOTS, (config.CUES_SORTED, config.HOUSE_ROOT)
        )
        self.assertEqual(config.CUED_DESTINATION_NAMES, {"Cues Sorted", "House"})

    def test_cued_roots_include_house_and_cues_sorted(self) -> None:
        from sorter import config, library

        self.assertEqual(
            library.cued_destination_roots(), [config.CUES_SORTED, config.HOUSE_ROOT]
        )

    def test_house_placement_counts_as_cued_destination(self) -> None:
        from sorter import library

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            house = root / "House"
            cs = root / "Cues Sorted"
            (house / "Amped").mkdir(parents=True)
            cs.mkdir()
            track = house / "Amped" / "01 - Artist - Song.flac"
            track.write_bytes(b"x")
            with patch.object(library, "HOUSE_ROOT", house), patch.object(
                library, "CUES_SORTED", cs
            ), patch.object(library, "LIBRARIES", {"House": house}):
                hits = library.find_cues_sorted_matches("Artist - Song.flac")
                self.assertEqual([h["path"] for h in hits], [str(track)])
                self.assertEqual(hits[0]["root_name"], "House")
                placement, _sets = library.cached_placement_indexes()
                self.assertTrue(library.find_cues_sorted_matches("Artist - Song.flac", index=placement))

    def test_sort_tree_lists_existing_subfolders_and_creates_nothing(self) -> None:
        from sorter import house_folders, library

        with tempfile.TemporaryDirectory() as tmp:
            house = Path(tmp) / "House"
            for existing in ("Amped", "Bassy", "Energy/Light", "Chill"):
                (house / existing).mkdir(parents=True)
            before = sorted(p.name for p in house.iterdir())
            with patch.object(library, "LIBRARIES", {"House": house}), patch.object(
                house_folders, "house_root", lambda: house
            ):
                tree = library.list_library_tree("House")
                names = [n["name"] for n in tree["folders"]]
                self.assertEqual(names, ["Amped", "Bassy", "Chill", "Energy"])
                energy = [n for n in tree["folders"] if n["name"] == "Energy"][0]
                self.assertEqual([c["relative_path"] for c in energy["children"]], ["Energy/Light"])
                self.assertIsNone(tree["new_folders"]["max"])  # no folder cap any more
                for bad in ("..", "Amped/../..", ""):
                    with self.assertRaises(ValueError):
                        library.resolve_destination("House", bad)
                dest = library.resolve_destination("House", "Amped", create=False)
                self.assertEqual(dest, (house / "Amped").resolve())
            self.assertEqual(sorted(p.name for p in house.iterdir()), before)

    def test_missing_house_root_is_not_created_by_listing(self) -> None:
        from sorter import library

        with tempfile.TemporaryDirectory() as tmp:
            house = Path(tmp) / "House"
            with patch.object(library, "LIBRARIES", {"House": house}):
                with self.assertRaises(FileNotFoundError):
                    library.list_library_tree("House")
            self.assertFalse(house.exists())

    def test_fs_guard_allows_house_only_when_not_readonly(self) -> None:
        from sorter import config, fs_guard

        target = config.HOUSE_ROOT / "Amped" / "x.flac"
        with patch.dict(os.environ, {"MUSIC_SORTER_READONLY": "1"}):
            self.assertFalse(fs_guard.is_allowed(target))
            with self.assertRaises(profile.ReadOnlyError):
                fs_guard._check("mkdir", config.HOUSE_ROOT / "Amped")
        with patch.dict(os.environ, {"MUSIC_SORTER_READONLY": "0"}):
            fs_guard._check("mkdir", config.HOUSE_ROOT / "Amped")  # no raise

    def test_directory_sort_label_under_house_subfolder(self) -> None:
        label = safety.directory_sort_label(
            "/Users/x/Music/DJ/Music/House/Amped/t.flac"
        )
        self.assertEqual(label, "Amped")
        self.assertEqual(safety.normalize_user2_dest(label), "Amped")
        self.assertEqual(
            safety.directory_sort_label("/Users/x/Music/DJ/Music/House/Energy/Housey/t.flac"),
            "Energy/Housey",
        )
        # Cues Sorted-style rejection list still applies to queue/archive heads.
        for head in ("Cues Sorted", "Cues Sorted House", "Add Cues", "Sets"):
            self.assertEqual(safety.normalize_user2_dest(head + "/x"), "")


class ReadonlyDatabaseGuardTests(unittest.TestCase):
    def test_assert_safe_to_write_refuses(self) -> None:
        with patch.dict(os.environ, {"MUSIC_SORTER_READONLY": "1"}):
            with self.assertRaises(safety.ReadOnlyModeError) as ctx:
                safety.assert_safe_to_write_vdj_database()
            self.assertIn("Read-only (VDJ open)", str(ctx.exception))

    def test_exclusive_lock_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            db.write_text("<VirtualDJ_Database/>")
            with patch.dict(os.environ, {"MUSIC_SORTER_READONLY": "1"}):
                with self.assertRaises(safety.ReadOnlyModeError):
                    with safety.vdj_database_exclusive_lock(db):
                        self.fail("lock must not be granted when read-only")
            self.assertEqual(list(Path(tmp).iterdir()), [db])

    def test_override_env_cannot_bypass_readonly(self) -> None:
        with patch.dict(
            os.environ, {"MUSIC_SORTER_READONLY": "1", "VDJ_ALLOW_RUNNING_WRITES": "1"}
        ):
            with self.assertRaises(safety.ReadOnlyModeError):
                safety.assert_safe_to_write_vdj_database()


class FsGuardTests(unittest.TestCase):
    def _run(self, code: str, notes: Path) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update(
            {
                "MUSIC_SORTER_READONLY": "1",
                "MUSIC_SORTER_PROFILE": "house",
                "MUSIC_SORTER_NOTES_DIR": str(notes),
                "PYTHONPATH": f"{ROOT}{os.pathsep}{UI_DIR}",
            }
        )
        return subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60
        )

    def test_blocks_writes_outside_notes_but_allows_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            notes = Path(tmp) / "Notes"
            notes.mkdir()
            probe = Path.home() / "Music" / "__house_fork_probe__.txt"
            code = f"""
import json, os, shutil
from pathlib import Path
from sorter import fs_guard
fs_guard.install()
out = {{}}
def attempt(name, fn):
    try:
        fn(); out[name] = "ALLOWED"
    except Exception as e:
        out[name] = type(e).__name__
p = Path({str(probe)!r})
attempt("open_w", lambda: open(p, "w").write("x"))
attempt("write_text", lambda: p.write_text("x"))
attempt("mkdir", lambda: Path({str(probe.parent / '__house_fork_dir__')!r}).mkdir())
attempt("rename", lambda: os.rename({str(probe)!r}, {str(probe) + '2'!r}))
attempt("unlink", lambda: os.unlink({str(probe)!r}))
attempt("move", lambda: shutil.move({str(probe)!r}, {str(probe) + '2'!r}))
attempt("notes_write", lambda: Path({str(notes / 'ok.json')!r}).write_text("{{}}"))
attempt("read_ok", lambda: open("/etc/hosts").read())
print(json.dumps(out))
"""
            proc = self._run(code, notes)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout.strip().splitlines()[-1])
            for key in ("open_w", "write_text", "mkdir", "rename", "unlink", "move"):
                self.assertNotEqual(out[key], "ALLOWED", key)
            self.assertEqual(out["notes_write"], "ALLOWED")
            self.assertEqual(out["read_ok"], "ALLOWED")
            self.assertTrue((notes / "ok.json").is_file())
            self.assertFalse(probe.exists())
            self.assertFalse((probe.parent / "__house_fork_dir__").exists())


def _node(script: str) -> dict:
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout.strip() or "{}")


class BpmCamelotHelperTests(unittest.TestCase):
    def _script(self, body: str) -> dict:
        return _node(
            f"""
const S = require({json.dumps(str(UI_STATIC / "state.js"))});
S.state.tracks = [
  {{ bpm: 122, camelot: "8a" }},      // 0
  {{ bpm: null, camelot: "" }},       // 1 no data
  {{ bpm: 118, camelot: "1B" }},      // 2
  {{ bpm: 120, camelot: "12A" }},     // 3
  {{ bpm: 121, camelot: "1A" }},      // 4
  {{ cues: {{ bpm: 125 }}, camelot: "bogus" }}, // 5
];
{body}
"""
        )

    def test_sort_bpm_missing_last_both_directions(self) -> None:
        out = self._script(
            """console.log(JSON.stringify({
              asc: S.sortHouseIndexes([0,1,2,3,4,5], "bpm", "asc"),
              desc: S.sortHouseIndexes([0,1,2,3,4,5], "bpm", "desc"),
            }));"""
        )
        self.assertEqual(out["asc"], [2, 3, 4, 0, 5, 1])
        self.assertEqual(out["desc"], [5, 0, 4, 3, 2, 1])

    def test_sort_camelot_number_major_missing_last(self) -> None:
        out = self._script(
            """console.log(JSON.stringify({
              asc: S.sortHouseIndexes([0,1,2,3,4,5], "camelot", "asc"),
              desc: S.sortHouseIndexes([0,1,2,3,4,5], "camelot", "desc"),
              vals: ["1A","1B","2A","12B"].map(S.camelotSortValue),
            }));"""
        )
        self.assertEqual(out["asc"][:4], [4, 2, 0, 3])
        self.assertEqual(set(out["asc"][4:]), {1, 5})
        self.assertEqual(out["desc"][:4], [3, 0, 2, 4])
        self.assertEqual(set(out["desc"][4:]), {1, 5})
        self.assertEqual(out["vals"], sorted(out["vals"]))

    def test_bpm_range_excludes_unknown(self) -> None:
        out = self._script(
            """console.log(JSON.stringify(
              [0,1,2,3,4,5].map((i) => S.bpmInRange(S.state.tracks[i], 115, 125))
            ));"""
        )
        self.assertEqual(out, [True, False, True, True, True, True])

    def test_no_filter_means_everything(self) -> None:
        out = self._script(
            """console.log(JSON.stringify(
              [0,1].map((i) => S.bpmInRange(S.state.tracks[i], null, null))
            ));"""
        )
        self.assertEqual(out, [True, True])


class ShippedUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (UI_STATIC / "index.html").read_text(encoding="utf-8")
        self.js = (UI_STATIC / "app.js").read_text(encoding="utf-8")

    def test_house_ui_has_labelled_controls(self) -> None:
        for el_id in ("bpmMinInput", "bpmMaxInput", "bpmChip115125", "bpmClearBtn", "houseSortKey", "houseSortDir", "houseCrateSelect", "readonlyBadge"):
            self.assertIn(f'id="{el_id}"', self.html, el_id)
        self.assertIn('for="bpmMinInput"', self.html)
        self.assertIn('for="bpmMaxInput"', self.html)
        self.assertIn("115–125", self.html)
        self.assertIn("Read-only (VDJ open)", self.html)
        self.assertIn("No tracks in this BPM range", self.js)

    def test_no_zouk_wording_in_user_facing_html(self) -> None:
        # Only internal ids remain (hidden inert button, hidden Assemble lane keys).
        low = self.html.lower()
        for token in ("zoukspeedbtn", "kizouk", "neo_zouk"):
            low = low.replace(token, "")
        self.assertNotIn("zouk", low)
        self.assertNotIn("Both", self.html.split("libraryPathSeg")[1][:300])

    def test_ui_never_sends_lane_to_sort(self) -> None:
        sort_call = self.js.split('api("/api/sort"', 1)[1][:600]
        self.assertNotIn("lane:", sort_call)

    def test_oh_picker_has_no_remembered_filter(self) -> None:
        # Folder picker starts unfiltered; stale filter text / keys are cleared.
        self.assertIn("function resetFolderPickerFilter()", self.js)
        self.assertIn('state.filter = "";', self.js)
        self.assertIn('window.addEventListener("pageshow", resetFolderPickerFilter)', self.js)
        self.assertNotIn('localStorage.setItem("folderFilter', self.js)
        self.assertNotIn('"Amped"', self.js.split("function resetFolderPickerFilter", 1)[1][:900])
        state_js = (UI_STATIC / "state.js").read_text(encoding="utf-8")
        self.assertIn('filter: ""', state_js)

    def test_ui_labels_house_as_cued_destination(self) -> None:
        self.assertIn("isCuedDestinationPath", self.js)
        self.assertIn("/Music/House/", self.js)
        self.assertNotIn("Pick the Gemini rec or an OH folder · Cues Sorted", self.html)
        self.assertIn("Pipeline: Add Cues → pick a House folder → COPY TO House (original stays).", self.html)

    def test_gemini_fallback_label(self) -> None:
        self.assertIn("gemini-3.8-flash", self.js)


if __name__ == "__main__":
    unittest.main()
