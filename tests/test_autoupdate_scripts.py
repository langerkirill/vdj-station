"""The Mac auto-updater's scripts ship inside every release (the test bundle) and are installed
from there with --refresh-updater: a release whose updater script does not even parse must not
pass the gate."""
import ast
import shutil
import subprocess
import unittest
from pathlib import Path

UPDATE = Path(__file__).resolve().parents[1] / "docs" / "autocue-lab" / "autoupdate"
SCRIPTS = ["autocue_update.sh", "run_sorter.sh", "install_autoupdate.sh", "build_release_assets.sh",
           "autocue_gate.py", "autocue_versions.py", "autocue_github.py"]


@unittest.skipUnless(UPDATE.is_dir(), "updater scripts not present")
class UpdaterScriptsTests(unittest.TestCase):
    def test_all_present(self):
        for name in SCRIPTS:
            self.assertTrue((UPDATE / name).is_file(), name)

    @unittest.skipUnless(shutil.which("bash"), "no bash")
    def test_shell_scripts_parse(self):
        for name in SCRIPTS:
            if name.endswith(".sh"):
                r = subprocess.run(["bash", "-n", str(UPDATE / name)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")

    def test_python_scripts_parse(self):
        for name in SCRIPTS:
            if name.endswith(".py"):
                ast.parse((UPDATE / name).read_text(encoding="utf-8"), filename=name)

    def test_the_updater_has_a_version_stamp(self):
        # --refresh-updater installs a release's updater only when its stamp is higher: raise
        # UPDATER_VERSION in autocue_update.sh whenever an updater script changes
        text = (UPDATE / "autocue_update.sh").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^UPDATER_VERSION=\d+$")

    def test_the_updater_installs_every_script_it_runs(self):
        text = (UPDATE / "autocue_update.sh").read_text(encoding="utf-8")
        listed = text.split('SCRIPTS="', 1)[1].split('"', 1)[0].split()
        for name in ("autocue_update.sh", "autocue_gate.py", "autocue_versions.py", "autocue_github.py", "run_sorter.sh"):
            self.assertIn(name, listed)
        installer = (UPDATE / "install_autoupdate.sh").read_text(encoding="utf-8")
        for name in listed:
            self.assertIn(name, installer, f"install_autoupdate.sh does not copy {name}")


if __name__ == "__main__":
    unittest.main()
