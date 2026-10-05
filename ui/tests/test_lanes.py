import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from sorter import house_folders
from sorter.lanes import ensure_sort_folder


class EnsureSortFolderTests(TestCase):
    """HOUSE FORK: destinations are EXISTING House subfolders (nested ok)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.house = Path(self._tmp.name) / "House"
        for d in ("Amped", "Bassy", "Energy/Light", "Chill/Journey", "R&B"):
            (self.house / d).mkdir(parents=True)
        self._p = patch.object(house_folders, "house_root", lambda: self.house)
        self._p.start()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def test_existing_folders_pass_and_snap_case(self) -> None:
        self.assertEqual(ensure_sort_folder("amped"), "Amped")
        self.assertEqual(ensure_sort_folder("Bassy/"), "Bassy")
        self.assertEqual(ensure_sort_folder("energy/LIGHT"), "Energy/Light")
        self.assertEqual(ensure_sort_folder("R&B", "pink"), "R&B")

    def test_every_listed_folder_is_accepted(self) -> None:
        from sorter import profile

        listed = profile.house_sort_folders()
        self.assertIn("Chill/Journey", listed)
        from sorter.house_folders import GROUP_ROOT_FOLDERS

        for name in listed:
            if name in GROUP_ROOT_FOLDERS:  # Chill / Energy are group headers, never destinations
                with self.assertRaises(ValueError):
                    ensure_sort_folder(name)
                continue
            self.assertEqual(ensure_sort_folder(name), name)

    def test_unknown_and_escaping_dests_are_rejected(self) -> None:
        for bad in ("Paolo Mac", "OH Vocal", "House+Zouk", "", "Amped/Sub", "../x", "Amped/.."):
            with self.assertRaises(ValueError, msg=bad):
                ensure_sort_folder(bad)
