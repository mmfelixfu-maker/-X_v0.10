import tempfile
import unittest
import zipfile
from pathlib import Path

from director.pulse_import import (
    compact_ai_catalog,
    import_waveform_source,
    parse_pulse_text,
)


SAMPLE = (
    "Dungeonlab+pulse:0,1,8="
    "0,20,8,1,1/0.00-1,50.00-0,100.00-1"
    "+section+0,20,8,1,0/0.00-1,100.00-1"
)


class PulseImportTests(unittest.TestCase):
    def test_parse_packs_four_25ms_points_into_hex_frame(self):
        parsed = parse_pulse_text(SAMPLE)
        self.assertEqual(parsed.sections, 1)
        self.assertTrue(parsed.frames)
        self.assertTrue(all(len(frame) == 16 for frame in parsed.frames))
        self.assertEqual(parsed.strengths[:3], [0, 50, 100])

    def test_import_zip_separates_valid_and_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / "waves.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("测试.pulse", SAMPLE)
                output.writestr("broken.pulse", "not a pulse")
            result = import_waveform_source(archive, base / "waveforms")
            self.assertEqual(result["accepted"], 1)
            self.assertEqual(result["rejected"], 1)
            summaries = compact_ai_catalog(base / "waveforms" / "catalog.json")
            self.assertEqual(len(summaries), 1)
            self.assertIn("测试", next(iter(summaries.values())))

    def test_invalid_format_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_pulse_text("hello")

    def test_legacy_export_without_metadata_separator(self):
        parsed = parse_pulse_text(
            "Dungeonlab+pulse:0,20,8,1,1/0.00-1,100.00-1"
        )
        self.assertEqual(parsed.sections, 1)
        self.assertTrue(parsed.frames)


if __name__ == "__main__":
    unittest.main()
