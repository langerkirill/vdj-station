"""Kirill 2026-10-08: every written loop must pass the wrap test where it is WRITTEN.

The seam gate ran on the candidate, but cue_writer then re-snapped the start to the grid
and could shorten the loop before the song end without testing again, so loops were
written at a spot/length that never passed "last 1-2 s into first 1-2 s".
"""
import unittest

from vdj_cuer.cue_hygiene import final_loop_seam_ok


class FinalLoopSeamTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def _retest(self, result):
        def retest(start, beats):
            self.calls.append((start, beats))
            return result
        return retest

    def test_unchanged_loop_keeps_its_passed_seam_without_retesting(self):
        ok, why = final_loop_seam_ok(48.0, 32, 48.0005, 32, self._retest(False))
        self.assertTrue(ok)
        self.assertEqual(self.calls, [])
        self.assertEqual(why, "")

    def test_grid_snap_moves_the_loop_so_the_seam_is_retested(self):
        ok, why = final_loop_seam_ok(48.0, 32, 48.04, 32, self._retest(True))
        self.assertTrue(ok)
        self.assertEqual(self.calls, [(48.04, 32)])

    def test_moved_loop_that_fails_the_retest_is_refused(self):
        ok, why = final_loop_seam_ok(48.0, 32, 48.04, 32, self._retest(False))
        self.assertFalse(ok)
        self.assertIn("48.040", why)

    def test_shortened_loop_is_retested_at_its_new_length(self):
        ok, _ = final_loop_seam_ok(200.0, 32, 200.0, 16, self._retest(False))
        self.assertFalse(ok)
        self.assertEqual(self.calls, [(200.0, 16)])


if __name__ == "__main__":
    unittest.main()
