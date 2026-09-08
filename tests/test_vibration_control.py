import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "vibration_control",
    ROOT / "vibration-control.py",
)
vibration_control = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = vibration_control
SPEC.loader.exec_module(vibration_control)


class PercentageCommandTests(unittest.TestCase):
    def test_percentage_command(self):
        args = vibration_control.build_parser().parse_args(
            ["--percent", "35"]
        )
        self.assertEqual(
            vibration_control.build_command(args),
            "PERCENT 35",
        )

    def test_percentage_range(self):
        args = vibration_control.build_parser().parse_args(
            ["--percent", "101"]
        )
        with self.assertRaises(ValueError):
            vibration_control.build_command(args)

    def test_percentage_channel_b(self):
        args = vibration_control.build_parser().parse_args(
            ["--channel", "B", "--percent", "35"]
        )
        self.assertEqual(
            vibration_control.build_command(args),
            "PERCENT B 35",
        )

    def test_percentage_both_channels(self):
        args = vibration_control.build_parser().parse_args(
            ["--channel", "AB", "--percent", "20"]
        )
        self.assertEqual(
            vibration_control.build_command(args),
            "PERCENT AB 20",
        )

    def test_percentage_with_duration_and_pattern(self):
        args = vibration_control.build_parser().parse_args([
            "--channel", "B", "--percent", "25",
            "--duration-ms", "1200", "--pattern", "PULSE",
        ])
        self.assertEqual(
            vibration_control.build_command(args),
            "PERCENT B 25 1200 PULSE",
        )

    def test_advanced_hit_is_percentage_compatibility_alias(self):
        args = vibration_control.build_advanced_parser().parse_args([
            "--hit", "100", "--time", "200", "--interval", "5",
        ])
        action = vibration_control.build_advanced_action(args)
        self.assertEqual(action(), "PERCENT 100 800 BUBBLE")

    def test_advanced_set_uses_longer_default_effect(self):
        args = vibration_control.build_advanced_parser().parse_args([
            "--set", "15", "--time", "20", "--interval", "0.5",
        ])
        action = vibration_control.build_advanced_action(args)
        self.assertEqual(action(), "PERCENT 15 3000 BUBBLE")

    def test_advanced_randomhit_accepts_zero_to_one_hundred(self):
        args = vibration_control.build_advanced_parser().parse_args([
            "--randomhit", "0-100", "--time", "120", "--interval", "2",
        ])
        with patch.object(
            vibration_control.random, "uniform", return_value=0,
        ):
            self.assertEqual(
                vibration_control.build_advanced_action(args)(),
                "PERCENT 0 800 BUBBLE",
            )


if __name__ == "__main__":
    unittest.main()
