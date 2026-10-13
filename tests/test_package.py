"""The vdj-autocue package (pyproject.toml) holds everything AutoCue imports.

The Mac installs AutoCue with pip from the GitHub Release v<version>; a module the package forgets
would only show up as a crash there. So: every module the packaged code imports is either in
the package, a declared dependency, or the standard library; the version is a valid release
version with a changelog section; the shipped cue model is included and not empty.
"""
import ast
import re
import sys
import unittest
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # the Mac's Python may be older: skip, never fail to collect
    tomllib = None

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8")) if tomllib else {}
SETUP = PYPROJECT.get("tool", {}).get("setuptools", {})
# import name -> the distribution that provides it (only where they differ)
IMPORT_NAMES = {"sklearn": "scikit-learn", "google": "google-genai", "dotenv": "python-dotenv"}


def _packaged_files():
    for pkg in SETUP["packages"]:
        yield from sorted((ROOT / pkg.replace(".", "/")).glob("*.py"))
    for mod in SETUP["py-modules"]:
        yield ROOT / f"{mod}.py"


def _top_level_imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


@unittest.skipIf(tomllib is None, "needs Python 3.11+ (tomllib)")
class PackageTests(unittest.TestCase):
    def test_every_import_is_packaged_declared_or_stdlib(self):
        packaged = {p.split(".")[0] for p in SETUP["packages"]} | set(SETUP["py-modules"])
        declared = {re.split(r"[<>=!~ \[;]", d, maxsplit=1)[0].lower() for d in PYPROJECT["project"]["dependencies"]}
        # imported inside functions from the Music Sorter UI that runs AutoCue (VDJ_SORTER_UI_DIR)
        provided_by_sorter = {"sorter"}
        missing = {}
        for path in _packaged_files():
            for name in _top_level_imports(path):
                if (name in sys.stdlib_module_names or name in packaged or name in provided_by_sorter
                        or IMPORT_NAMES.get(name, name).lower() in declared):
                    continue
                missing.setdefault(name, []).append(path.relative_to(ROOT).as_posix())
        self.assertEqual(missing, {}, "add the module to pyproject.toml (package or dependency)")

    def test_every_packaged_file_exists(self):
        for path in _packaged_files():
            self.assertTrue(path.is_file(), path)
        for pkg in SETUP["packages"]:
            self.assertTrue((ROOT / pkg.replace(".", "/") / "__init__.py").is_file(), pkg)

    def test_version_is_a_release_version_with_a_changelog_section(self):
        from vdj_cuer import __version__

        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")
        self.assertEqual(PYPROJECT["tool"]["setuptools"]["dynamic"]["version"]["attr"], "vdj_cuer._version.__version__")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(changelog, rf"(?m)^## {re.escape(__version__)}\b")

    def test_shipped_model_is_package_data_and_not_empty(self):
        data = SETUP["package-data"]["vdj_cuer.ml"]
        for name in ("artifacts/cue_bar_clf.joblib", "artifacts/cue_bar_clf.json"):
            self.assertIn(name, data)
            self.assertGreater((ROOT / "vdj_cuer" / "ml" / name).stat().st_size, 0, name)


if __name__ == "__main__":
    unittest.main()
