"""Kirill 2026-10-08: "multiple cues start on sections where voices are coming in, so I can't
cue jump to those sections because it would just sound weird."

A sung line that STARTS within the last beat before the cue (a pickup into the downbeat) and is
still sounding at the press gets clipped by a jump. On Kirill's cue deletes/moves vs kept cues
(13 vocal songs) this flagged 7/34 rejected cues and 0/25 kept ones; a line already running for
more than a beat, or one starting on/after the press, is left alone.
"""
import dataclasses
import unittest

from vdj_cuer.cue_hygiene import resolve_half_entered, vocal_line_starts_before
from vdj_cuer.stem_evidence import VOICE_FINE_HOP_SECONDS, StemProfile

HOP = VOICE_FINE_HOP_SECONDS
BEAT = 0.47  # 128 BPM


def _vocal(segments, seconds=40.0):
    """Voice-band fine envelope: 10.0 inside the (start, end) sung segments, 0.1 elsewhere."""
    n = int(seconds / HOP)
    fine = [0.1] * n
    for a, b in segments:
        for i in range(round(a / HOP), min(n, round(b / HOP))):
            fine[i] = 10.0
    base = StemProfile.from_frames([0.0] * int(seconds / 0.25), 0.25)  # old low-band: silent
    return dataclasses.replace(base, voice_fine_frames=tuple(fine), voice_fine_reference=10.0)


class PickupTests(unittest.TestCase):
    def test_line_starting_a_fraction_of_a_beat_before_the_cue_is_coming_in(self):
        self.assertTrue(vocal_line_starts_before(_vocal([(19.7, 24.0)]), 20.0, BEAT))

    def test_line_starting_on_the_press_is_a_clean_entry(self):
        self.assertFalse(vocal_line_starts_before(_vocal([(20.0, 24.0)]), 20.0, BEAT))

    def test_line_already_running_for_over_a_beat_is_not_a_pickup(self):
        self.assertFalse(vocal_line_starts_before(_vocal([(18.8, 24.0)]), 20.0, BEAT))

    def test_line_that_ended_before_the_press_is_not_a_pickup(self):
        self.assertFalse(vocal_line_starts_before(_vocal([(19.6, 19.85)]), 20.0, BEAT))

    def test_without_a_fine_envelope_nothing_changes(self):
        plain = StemProfile.from_frames([0.0] * 160, 0.25)
        self.assertFalse(vocal_line_starts_before(plain, 20.0, BEAT))


class ResolveWithPickupTests(unittest.TestCase):
    def test_pickup_cue_moves_back_to_a_quiet_bar(self):
        prof = _vocal([(19.7, 24.0)])
        new_t, why = resolve_half_entered(prof, 20.0, BEAT)
        self.assertAlmostEqual(new_t, 20.0 - 4 * BEAT, places=3)
        self.assertIn("coming in", why)

    def test_pickup_with_no_quiet_bar_nearby_is_skipped(self):
        prof = _vocal([(15.0, 19.0), (19.7, 24.0)])  # singing through both earlier bars' [1]
        new_t, why = resolve_half_entered(prof, 20.0, BEAT)
        self.assertIsNone(new_t)

    def test_clean_entry_is_kept_where_it_is(self):
        self.assertEqual(resolve_half_entered(_vocal([(20.0, 24.0)]), 20.0, BEAT), (20.0, ""))


if __name__ == "__main__":
    unittest.main()
