import tempfile
import unittest
from pathlib import Path

from director.waveforms import load_library, remember_waveform, validate_sequence


class WaveformLibraryTests(unittest.TestCase):
    def test_high_then_rest_cycle_is_valid(self):
        steps = validate_sequence([
            {
                "mode": "fixed", "relative_percent": 100,
                "duration_ms": 5000, "interval_ms": 500,
                "pulse_duration_ms": 500, "pattern": "RHYTHM",
            },
            {"mode": "rest", "duration_ms": 2000},
        ])
        self.assertEqual(steps[1]["mode"], "rest")

    def test_remembered_waveform_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "waveforms.json"
            remember_waveform(path, "learned_one", "learned", [
                {
                    "mode": "random", "min_relative_percent": 10,
                    "max_relative_percent": 60, "duration_ms": 3000,
                    "interval_ms": 800, "pulse_duration_ms": 500,
                    "pattern": "BUBBLE",
                }
            ])
            self.assertIn("learned_one", load_library(path)["waveforms"])

    def test_invalid_random_range_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_sequence([
                {
                    "mode": "random", "min_relative_percent": 80,
                    "max_relative_percent": 20, "duration_ms": 1000,
                    "interval_ms": 500, "pulse_duration_ms": 500,
                    "pattern": "PULSE",
                }
            ])


if __name__ == "__main__":
    unittest.main()
