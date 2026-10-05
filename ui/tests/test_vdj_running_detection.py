"""VirtualDJ-running detection looks at the process EXECUTABLE (ps comm), never argv."""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import vdj_database_safety as V


def fake_ps(lines: str, rc: int = 0):
    return lambda *a, **k: SimpleNamespace(returncode=rc, stdout=lines, stderr="")


class VdjRunningDetectionTests(unittest.TestCase):
    def check(self, ps_out: str) -> bool:
        with patch.object(V.subprocess, "run", fake_ps(ps_out)) as _:
            return V.is_virtualdj_running()

    def test_argv_mentioning_virtualdj_app_does_not_count(self):
        # ps -o comm= only shows the executable, so a shell/agent that merely mentions
        # VirtualDJ.app in its command line shows up as zsh/bash/grep/python.
        out = "  101 /bin/zsh\n  102 /usr/bin/grep\n  103 /usr/bin/pgrep\n  104 /Users/k/venv/bin/python\n"
        self.assertFalse(self.check(out))

    def test_ps_is_asked_for_comm_not_command(self):
        calls = []

        def rec(cmd, **k):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(V.subprocess, "run", rec):
            V.is_virtualdj_running()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "ps")
        self.assertIn("pid=,comm=", calls[0])
        self.assertNotIn("command=", " ".join(calls[0]))
        self.assertNotIn("pgrep", calls[0])

    def test_real_executable_path_counts(self):
        out = "  1 /sbin/launchd\n 555 /Applications/VirtualDJ.app/Contents/MacOS/VirtualDJ\n"
        self.assertTrue(self.check(out))

    def test_bare_comm_and_exe_exact_only(self):
        self.assertTrue(self.check("  7 VirtualDJ\n"))
        self.assertTrue(self.check("  7 VirtualDJ.exe\n"))
        self.assertFalse(self.check("  7 /tmp/VirtualDJ.exe.bak\n"))
        self.assertFalse(self.check("  7 /Users/k/Library/Application Support/VirtualDJ/tool\n"))
        self.assertFalse(self.check("  7 /usr/bin/VirtualDJHelperX\n"))

    def test_ps_failure_is_not_running(self):
        with patch.object(V.subprocess, "run", side_effect=OSError("no ps")):
            self.assertFalse(V.is_virtualdj_running())
        self.assertFalse(self.check(""))

    def test_other_gates_share_the_same_detector(self):
        from sorter import relocate as R
        import vdj_cue_patch as C

        with patch.object(V.subprocess, "run", fake_ps("  7 /bin/zsh\n")):
            self.assertFalse(R.is_virtualdj_running())
            self.assertFalse(C.is_virtualdj_running())
        with patch.object(V.subprocess, "run", fake_ps("  9 /Applications/VirtualDJ.app/Contents/MacOS/VirtualDJ\n")):
            self.assertTrue(R.is_virtualdj_running())
            self.assertTrue(C.is_virtualdj_running())

    def test_live_process_with_virtualdj_app_in_argv_is_ignored(self):
        p = subprocess.Popen(["sleep", "5", "/Applications/VirtualDJ.app/Contents/MacOS/VirtualDJ"])
        try:
            self.assertFalse(V.is_virtualdj_running())
        finally:
            p.kill()


if __name__ == "__main__":
    unittest.main()
