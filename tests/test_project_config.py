import unittest
from pathlib import Path

from director.project_config import load_project_config


ROOT = Path(__file__).resolve().parents[1]


class ProjectConfigTests(unittest.TestCase):
    def test_feedback_has_single_percentage_unit(self):
        config = load_project_config(ROOT / "project-config.json")
        self.assertEqual(config["feedback"]["intensity_unit"], "percent")
        self.assertTrue(
            10 <= config["feedback"]["min_pulse_duty_percent"] <= 100
        )
        self.assertTrue(
            3 <= config["feedback"]["ai_response_timeout_seconds"] <= 120
        )
        self.assertEqual(config["story_pacing"], "slow")
        self.assertTrue(
            1 <= config["feedback"]["reaction_observation_seconds"] <= 10
        )

    def test_channels_live_in_the_unified_config(self):
        config = load_project_config(ROOT / "project-config.json")
        self.assertIn("A", config["feedback"]["channels"])
        self.assertNotIn("feedback_channels", config)


if __name__ == "__main__":
    unittest.main()
