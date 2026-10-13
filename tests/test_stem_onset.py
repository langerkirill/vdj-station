import unittest
from vdj_cuer.stem_color import classify_onset

L_ON = {"kick": "high", "hihat": "medium", "vocal": "none", "bass": "medium", "instruments": "medium"}


class OnsetRuleTests(unittest.TestCase):
    def test_low_drum_onset_is_blue_and_never_a_drop(self):
        # Aurores 260.627: kick falls away, vocals come in.
        c = classify_onset({**L_ON, "kick": "low", "vocal": "medium"},
                           {"kick": 0.17, "hihat": 0.37}, {"kick": 0.82, "hihat": 0.34})
        self.assertEqual(c["color"], "orange")  # vocals, no drums
        self.assertFalse(c["drop_ok"])

    def test_no_drums_first_bar_even_if_later_bars_have_kick(self):
        # Deep Strings 173.115: kick only enters ~4 beats in.
        c = classify_onset(L_ON, {"kick": 0.37, "hihat": 0.31},
                           {"kick": 0.0, "hihat": 0.0},
                           {"kick": 0.02, "hihat": 0.05})
        self.assertFalse(c["drums"])
        self.assertEqual(c["color"], "blue")
        self.assertFalse(c["drop_ok"])

    def test_new_high_energy_section_is_a_drop(self):
        # Deep Strings 173.115: silence into kick.
        c = classify_onset(L_ON, {"kick": 0.37, "hihat": 0.31}, {"kick": 0.0, "hihat": 0.0})
        self.assertTrue(c["drop_ok"])
        self.assertEqual(c["color"], "green")

    def test_no_step_up_is_not_a_drop(self):
        # Em Pessoa 186.995: already at full drums before the cue.
        c = classify_onset(L_ON, {"kick": 0.77, "hihat": 0.46}, {"kick": 0.79, "hihat": 0.38})
        self.assertFalse(c["drop_ok"])
        self.assertEqual(c["color"], "green")

    def test_hat_only_onset_below_threshold_is_blue(self):
        c = classify_onset(L_ON, {"kick": 0.06, "hihat": 0.19}, {"kick": 0.1, "hihat": 0.47})
        self.assertEqual(c["color"], "blue")

    def test_vocals_with_drums_yellow(self):
        c = classify_onset({**L_ON, "vocal": "high"}, {"kick": 0.8, "hihat": 0.3}, None)
        self.assertEqual(c["color"], "yellow")


if __name__ == "__main__":
    unittest.main()
