"""Kirill 2026-10-08, California Dreaming: "all the cues ... landed on vocals that were interrupted".

The old mid-word test read the 800 Hz peak envelope, which reads higher voices as quiet, so it
missed 4 of the 6 cues. The new test reads the vocal stem's 250-4000 Hz envelope (20 ms):
a cue is "sung through" when the vocal keeps going for the first beat after the press AND no
fresh, louder line starts on the press (a line entering on the downbeat is a clean entry,
R-90). Flagged cues move to a quiet bar within 2 bars, earlier or later (R-88/R-89), and are
skipped when there is none. Bench (bench/cloud/entry_bench + proposals/voiceband-midword):
Kirill's deleted/moved cues flagged 18/58 (production 12/58), kept cues flagged 3/65
(production 9/65), California Dreaming 6/6 (production 2/6).
"""
import dataclasses
import unittest
from pathlib import Path

from vdj_cuer.cue_hygiene import _entry_problem, resolve_half_entered, vocal_sung_through
from vdj_cuer.stem_evidence import VOICE_FINE_HOP_SECONDS, StemProfile

HOP = VOICE_FINE_HOP_SECONDS
BEAT = 0.47  # 128 BPM
BAR = 4 * BEAT


def _vocal(segments, seconds=60.0, level=10.0):
    """Fine voice band: ``level`` inside (start, end[, level]) segments, 0.1 elsewhere; the
    old 800 Hz envelope stays silent (a high voice it cannot see)."""
    n = int(seconds / HOP)
    fine = [0.1] * n
    for seg in segments:
        a, b = seg[0], seg[1]
        v = seg[2] if len(seg) > 2 else level
        for i in range(round(a / HOP), min(n, round(b / HOP))):
            fine[i] = v
    base = StemProfile.from_frames([0.0] * int(seconds / 0.25), 0.25)
    return dataclasses.replace(base, voice_fine_frames=tuple(fine), voice_fine_reference=10.0)


class SungThroughTests(unittest.TestCase):
    def test_line_running_through_the_press_is_flagged(self):
        prof = _vocal([(15.0, 26.0)])
        self.assertTrue(vocal_sung_through(prof, 20.0, BEAT))
        self.assertIn("sung through", _entry_problem(prof, 20.0, BEAT))

    def test_fresh_louder_line_on_the_press_is_a_clean_entry(self):
        prof = _vocal([(15.0, 19.96, 4.0), (19.96, 26.0, 10.0)])  # quieter bed, lead enters on the 1
        self.assertFalse(vocal_sung_through(prof, 20.0, BEAT))

    def test_line_ending_at_the_press_is_not_flagged(self):
        prof = _vocal([(15.0, 20.05)])  # a tail that stops right after the press
        self.assertFalse(vocal_sung_through(prof, 20.0, BEAT))

    def test_silence_is_not_flagged(self):
        self.assertFalse(vocal_sung_through(_vocal([]), 20.0, BEAT))

    def test_without_a_fine_envelope_the_old_rule_still_applies(self):
        plain = StemProfile.from_frames([0.0] * 400, 0.25)
        self.assertFalse(vocal_sung_through(plain, 20.0, BEAT))
        self.assertEqual(_entry_problem(plain, 20.0, BEAT), "")


class RelocationTests(unittest.TestCase):
    def test_moves_back_one_bar_when_that_bar_is_quiet(self):
        prof = _vocal([(0.0, 17.5), (18.9, 30.0)])  # gap around 1 bar earlier (18.12)
        new_t, why = resolve_half_entered(prof, 20.0, BEAT)
        self.assertAlmostEqual(new_t, 20.0 - BAR, places=3)
        self.assertIn("moved", why)

    def test_never_moves_forward_even_when_only_the_next_bar_is_clean(self):
        # R-88 (2026-10-12): Kirill reversed both live +1-bar moves; a later bar is off his
        # phrase. Singing 10-21.3 s, 1-2 bars back are sung -> skip, never 20 + BAR.
        prof = _vocal([(10.0, 21.3)])
        new_t, why = resolve_half_entered(prof, 20.0, BEAT)
        self.assertIsNone(new_t)
        self.assertIn("skipped", why)

    def test_a_breath_inside_a_line_is_not_a_landing_spot(self):
        # the only gap near the cue is a 0.3 s breath at the bar before it
        prof = _vocal([(10.0, 18.0), (18.35, 40.0)])
        new_t, _why = resolve_half_entered(prof, 20.0, BEAT)
        self.assertIsNone(new_t)

    def test_line_entering_on_an_earlier_bar_is_a_landing_spot(self):
        # a rest, then a line starts exactly on the bar before the cue and is sung through it
        t1 = 20.0 - BAR
        prof = _vocal([(10.0, t1 - 0.5), (t1 - 0.02, 40.0)])
        new_t, _why = resolve_half_entered(prof, 20.0, BEAT)
        self.assertAlmostEqual(new_t, t1, places=3)

    def test_no_clean_spot_within_two_bars_skips(self):
        prof = _vocal([(5.0, 40.0)])
        new_t, why = resolve_half_entered(prof, 20.0, BEAT)
        self.assertIsNone(new_t)
        self.assertIn("skipped", why)

    def test_never_moves_before_the_song_origin(self):
        prof = _vocal([(0.0, 21.0)])
        new_t, _why = resolve_half_entered(prof, 20.0, BEAT, origin=19.5)
        self.assertNotEqual(new_t, 20.0 - BAR)


BENCH = Path(__file__).resolve().parents[1] / "bench" / "features"


@unittest.skipUnless((BENCH / "8f3d35efb8c4.npz").is_file(), "bench features not present")
class BenchTests(unittest.TestCase):
    """Production code on Kirill's labeled cues (bench/labels/entry.json), no audio."""

    def test_california_dreaming_cues_are_flagged(self):
        from bench.cloud.features import load_profiles, song_info

        info = song_info("8f3d35efb8c4")
        v = load_profiles("8f3d35efb8c4")["vocal"]
        cues = [p["pos"] for p in info["pois"] if p["type"] == "cue"]
        flagged = [t for t in cues if _entry_problem(v, t, info["beat_seconds"])]
        self.assertGreaterEqual(len(flagged), 5, flagged)

    def test_kept_vs_rejected_and_no_song_emptied(self):
        from bench.cloud.entry_bench import evaluate, rows, survival
        from vdj_cuer.stem_evidence import vocal_stem_is_usable

        def rule(P, t, beat):
            return bool(vocal_stem_is_usable(P) and _entry_problem(P["vocal"], t, beat))

        def resolver(P, t, beat):
            if not vocal_stem_is_usable(P):
                return t, ""
            return resolve_half_entered(P["vocal"], t, beat)

        res = evaluate(rule)
        self.assertGreaterEqual(res["bad_flagged"], 17, res)   # production gated: 12/58
        self.assertLessEqual(res["good_flagged"], 4, res)      # production gated: 9/65
        dead = [s for s, c in survival(resolver).items() if c["kept"] + c["moved"] == 0]
        # California Dreaming and How Deep Is Your Love keep cues (moved within 2 bars). Gorgon
        # City's two kept spots are flagged by the 2026-10-08 pickup rule (not this change), so
        # per R-88 every proposal there is skipped; the empty-plan guard then writes nothing.
        self.assertEqual([d for d in dead if "Gorgon City" not in d], [])
        self.assertGreater(len(rows()), 100)


if __name__ == "__main__":
    unittest.main()


class MovedCueSpacingTests(unittest.TestCase):
    """R-08: cues at least 32 beats apart. A cue moved by the vocal rule never crowds another."""

    def _P(self, pos, name):
        from vdj_cuer.cue_writer import PreparedPoi

        return PreparedPoi(kind="cue", name=name, position=pos, color_name="green",
                           color_value="0", elements=[])

    def test_moved_cue_too_close_to_another_is_dropped(self):
        from vdj_cuer.cue_hygiene import drop_crowded_moved_cues

        beat = 0.5  # 32 beats = 16 s
        cues = [self._P(10.0, "A"), self._P(24.0, "B moved"), self._P(42.0, "C")]
        kept, dropped = drop_crowded_moved_cues(cues, {24.0}, beat)
        self.assertEqual([c.name for c in kept], ["A", "C"])
        self.assertEqual([c.name for c in dropped], ["B moved"])

    def test_unmoved_cues_and_well_spaced_moves_are_kept(self):
        from vdj_cuer.cue_hygiene import drop_crowded_moved_cues

        cues = [self._P(10.0, "A"), self._P(28.0, "B moved"), self._P(44.0, "C")]
        kept, dropped = drop_crowded_moved_cues(cues, {28.0}, 0.5)
        self.assertEqual([c.name for c in kept], ["A", "B moved", "C"])
        self.assertEqual(dropped, [])
