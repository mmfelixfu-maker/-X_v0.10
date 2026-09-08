import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TEST_IMAGE = Path(__file__).resolve().parent / "fixture.jpg"
SPEC = importlib.util.spec_from_file_location(
    "ai_director",
    ROOT / "ai-director.py",
)
ai_director = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = ai_director
SPEC.loader.exec_module(ai_director)


class DecisionTests(unittest.TestCase):
    def test_valid_hit(self):
        decision = ai_director.Decision.from_json(
            '{"action":"hit","intensity_percent":35,'
            '"scene":"s","story":"t","reason":"r",'
            '"save_snapshot":false}'
        )
        self.assertEqual(
            ai_director.validate_decision(decision).intensity_percent,
            35,
        )

    def test_invalid_hit_level(self):
        decision = ai_director.Decision.from_json(
            '{"action":"hit","intensity_percent":0,'
            '"scene":"s","story":"t","reason":"r",'
            '"save_snapshot":false}'
        )
        with self.assertRaises(ValueError):
            ai_director.validate_decision(decision)

    def test_none_level_is_normalized(self):
        decision = ai_director.Decision.from_json(
            '{"action":"none","intensity_percent":80,'
            '"scene":"s","story":"t","reason":"r",'
            '"save_snapshot":false}'
        )
        self.assertEqual(
            ai_director.validate_decision(decision).intensity_percent,
            0,
        )

    def test_ai_intensity_is_always_percentage(self):
        decision = ai_director.Decision.from_json(
            '{"action":"hit","intensity_percent":100,'
            '"scene":"s","story":"t","reason":"r",'
            '"save_snapshot":false}'
        )
        self.assertEqual(
            ai_director.validate_decision(decision).intensity_percent,
            100,
        )


class DeepSeekRequestTests(unittest.TestCase):
    def test_request_uses_official_vision_format(self):
        config = ai_director.load_ai_config()
        response = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"action":"none","intensity_percent":0,'
                            '"scene":"s","story":"t",'
                            '"reason":"r","save_snapshot":false}'
                        )
                    }
                }
            ]
        }

        with (
            patch.dict(
                os.environ,
                {config["api_key_env"]: "test-key"},
            ),
            patch.object(
                ai_director,
                "api_request",
                return_value=response,
            ) as request,
            patch.object(
                ai_director,
                "load_session_preferences",
                return_value={
                    "feedback_device_location": "未指定",
                    "game_style": "default",
                },
            ),
        ):
            ai_director.ask_deepseek(
                config,
                TEST_IMAGE,
                [],
            )

        payload = request.call_args.args[3]
        self.assertIn(
            "【开场】",
            payload["messages"][0]["content"],
        )
        self.assertIn(
            "没有输入或画面暂时没有明显变化都不代表暂停",
            payload["messages"][0]["content"],
        )
        self.assertIn(
            "展示端口、脚本名、命令行、后台进程或内部控制参数",
            payload["messages"][0]["content"],
        )
        self.assertIn(
            "none 只表示本轮不新建或替换反馈，不表示停止主持",
            payload["messages"][0]["content"],
        )
        self.assertIn(
            "这是游戏第一轮",
            payload["messages"][1]["content"][0]["text"],
        )
        self.assertIn(
            "反馈设备位置",
            payload["messages"][1]["content"][0]["text"],
        )
        self.assertIn(
            "使用剧本默认风格",
            payload["messages"][1]["content"][0]["text"],
        )
        self.assertIn(
            "铺垫/试探阶段",
            payload["messages"][1]["content"][0]["text"],
        )
        image = payload["messages"][1]["content"][1]
        self.assertEqual(image["type"], "image_url")
        self.assertTrue(
            image["image_url"]["url"].startswith(
                "data:image/jpeg;base64,"
            )
        )
        self.assertEqual(
            image["image_url"]["detail"],
            "auto",
        )
        self.assertEqual(
            payload["response_format"],
            {"type": "json_object"},
        )
        self.assertEqual(
            payload["thinking"],
            {"type": "disabled"},
        )
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["max_tokens"], 5000)

    def test_openai_provider_uses_compatible_token_field(self):
        config = ai_director.load_ai_config()
        config.update({
            "provider": "openai",
            "label": "OpenAI",
            "model": "vision-test",
            "api_key_env": "OPENAI_API_KEY",
            "thinking_disabled": False,
            "token_field": "max_completion_tokens",
        })
        response = {
            "choices": [{"message": {"content": (
                '{"action":"none","intensity_percent":0,'
                '"scene":"s","story":"t","reason":"r",'
                '"save_snapshot":false}'
            )}}]
        }
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch.object(ai_director, "api_request", return_value=response) as request,
        ):
            ai_director.ask_provider(config, TEST_IMAGE, [])
        payload = request.call_args.args[3]
        self.assertEqual(payload["max_completion_tokens"], 5000)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("thinking", payload)

    def test_all_configured_providers_are_available(self):
        config = ai_director.load_ai_config()
        self.assertEqual(
            set(config["available_providers"]),
            {"custom", "deepseek", "openai"},
        )


class DeviceCommandTests(unittest.TestCase):
    def setUp(self):
        self._state_directory = tempfile.TemporaryDirectory()
        self._state_patch = patch.object(
            ai_director,
            "FEEDBACK_STATE_FILE",
            Path(self._state_directory.name) / "feedback-state.json",
        )
        self._state_patch.start()

    def tearDown(self):
        self._state_patch.stop()
        self._state_directory.cleanup()

    def test_ai_can_select_imported_pulse(self):
        decision = ai_director.Decision(
            action="hit", intensity_percent=55, scene="s", story="t",
            reason="r", save_snapshot=False, duration_ms=30000,
            feedback_mode="waveform",
            waveform_name="pulse-custom-1234567890",
        )
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(
                '{"version":1,"waveforms":{"pulse-custom-1234567890":'
                '{"enabled_for_ai":true,"frames":["0A0A0A0A00000000"]}},'
                '"rejected":[]}',
                encoding="utf-8",
            )
            with (
                patch.object(ai_director, "PULSE_CATALOG_FILE", catalog),
                patch.object(ai_director, "load_max_effect_duration_ms", return_value=600000),
                patch.object(ai_director.subprocess, "run") as run,
            ):
                ai_director.validate_decision(decision)
                run.return_value.stdout = ""
                run.return_value.stderr = ""
                run.return_value.returncode = 0
                ai_director.execute_decision(decision, True)
            command = run.call_args.args[0]
            self.assertEqual(
                command[command.index("--pulse-id") + 1],
                "pulse-custom-1234567890",
            )
            self.assertEqual(command[command.index("--time") + 1], "30")

    def test_ai_waveform_sequence_is_remembered_and_played(self):
        decision = ai_director.Decision(
            action="hit", intensity_percent=90, scene="s", story="t",
            reason="r", save_snapshot=False, duration_ms=120000,
            feedback_mode="waveform", waveform_name="high_rest_test",
            waveform_description="five seconds high, two seconds rest",
            remember_waveform=True,
            feedback_sequence=[
                {
                    "mode": "fixed", "relative_percent": 100,
                    "duration_ms": 5000, "interval_ms": 500,
                    "pulse_duration_ms": 500, "pattern": "RHYTHM",
                },
                {"mode": "rest", "duration_ms": 2000},
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / "library.json"
            current = Path(directory) / "current.json"
            with (
                patch.object(
                    ai_director, "load_max_effect_duration_ms",
                    return_value=200000,
                ),
                patch.object(ai_director, "WAVEFORM_LIBRARY_FILE", library),
                patch.object(ai_director, "CURRENT_WAVEFORM_FILE", current),
                patch.object(ai_director, "load_min_pulse_duty_percent", return_value=70),
                patch.object(ai_director.subprocess, "run") as run,
            ):
                ai_director.validate_decision(decision)
                run.return_value.stdout = ""
                run.return_value.stderr = ""
                run.return_value.returncode = 0
                ai_director.execute_decision(decision, True)
            self.assertTrue(current.exists())
            remembered = ai_director.load_library(library)
            self.assertIn("high_rest_test", remembered["waveforms"])
            command = run.call_args.args[0]
            self.assertIn("waveform-control.py", command[1])
            self.assertEqual(command[command.index("--time") + 1], "120")
            self.assertEqual(command[command.index("--min-duty") + 1], "70")
            self.assertIn("--background", command)

    def test_ai_continuous_random_uses_advanced_background(self):
        decision = ai_director.Decision(
            action="hit", intensity_percent=80, scene="s", story="t",
            reason="r", save_snapshot=False, duration_ms=10000,
            pattern="RHYTHM", feedback_mode="continuous_random",
            min_intensity_percent=30, interval_ms=2000,
            pulse_duration_ms=800,
        )
        with patch.object(
            ai_director, "load_max_effect_duration_ms", return_value=10000,
        ):
            ai_director.validate_decision(decision)
        with patch.object(ai_director.subprocess, "run") as run:
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            run.return_value.returncode = 0
            ai_director.execute_decision(decision, True)
        command = run.call_args.args[0]
        self.assertIn("--advanced", command)
        self.assertEqual(command[command.index("--randomhit") + 1], "30-80")
        self.assertEqual(command[command.index("--time") + 1], "10")
        self.assertEqual(command[command.index("--interval") + 1], "2")
        self.assertIn("--background", command)


class FeedbackWatchdogTests(unittest.TestCase):
    def setUp(self):
        self._state_directory = tempfile.TemporaryDirectory()
        self._state_patch = patch.object(
            ai_director,
            "FEEDBACK_STATE_FILE",
            Path(self._state_directory.name) / "feedback-state.json",
        )
        self._state_patch.start()

    def tearDown(self):
        self._state_patch.stop()
        self._state_directory.cleanup()

    def test_late_ai_response_is_changed_to_stop(self):
        late_decision = ai_director.Decision(
            action="hit", intensity_percent=50, scene="s", story="t",
            reason="late", save_snapshot=False,
        )

        def slow_response(*_args):
            time.sleep(0.05)
            return late_decision

        state = []
        with (
            patch.object(ai_director, "ask_ai", side_effect=slow_response),
            patch.object(ai_director, "load_ai_response_timeout_seconds", return_value=0.01),
            patch.object(ai_director, "stop_feedback_state") as stop,
            patch.object(ai_director, "execute_decision") as execute,
            patch.object(ai_director, "save_state"),
        ):
            result = ai_director.run_round(
                TEST_IMAGE, state, execute=True,
            )
        self.assertEqual(result.action, "stop")
        self.assertEqual(result.intensity_percent, 0)
        stop.assert_called_once()
        self.assertEqual(execute.call_args.args[0].action, "stop")

    def test_api_failure_stops_feedback_immediately(self):
        with (
            patch.object(ai_director, "ask_ai", side_effect=ConnectionError("offline")),
            patch.object(ai_director, "stop_feedback_state") as stop,
        ):
            with self.assertRaises(ConnectionError):
                ai_director.run_round(TEST_IMAGE, [], execute=True)
        stop.assert_called_once_with("AI request failed or disconnected")

    def test_ai_continuous_clamps_inverted_range(self):
        decision = ai_director.Decision(
            action="hit", intensity_percent=40, scene="s", story="t",
            reason="r", save_snapshot=False, duration_ms=10000,
            feedback_mode="continuous_random", min_intensity_percent=60,
            interval_ms=1000, pulse_duration_ms=800,
        )
        with patch.object(
            ai_director, "load_max_effect_duration_ms", return_value=10000,
        ):
            normalized = ai_director.validate_decision(decision)
        self.assertEqual(normalized.min_intensity_percent, 40)

    def test_ai_continuous_normalizes_zero_timing_fields(self):
        decision = ai_director.Decision(
            action="hit", intensity_percent=40, scene="s", story="t",
            reason="r", save_snapshot=False, duration_ms=0,
            feedback_mode="continuous_random", min_intensity_percent=0,
            interval_ms=0, pulse_duration_ms=0,
        )
        with patch.object(
            ai_director, "load_max_effect_duration_ms", return_value=200000,
        ):
            normalized = ai_director.validate_decision(decision)
        self.assertEqual(normalized.duration_ms, 10000)
        self.assertEqual(normalized.interval_ms, 1000)
        self.assertEqual(normalized.pulse_duration_ms, 800)
        self.assertEqual(normalized.min_intensity_percent, 0)

    def test_ai_continuous_enforces_minimum_pulse_duty(self):
        decision = ai_director.Decision(
            action="hit", intensity_percent=50, scene="s", story="t",
            reason="r", save_snapshot=False, duration_ms=10000,
            feedback_mode="continuous_fixed", interval_ms=2000,
            pulse_duration_ms=100,
        )
        with (
            patch.object(
                ai_director, "load_max_effect_duration_ms",
                return_value=200000,
            ),
            patch.object(
                ai_director, "load_min_pulse_duty_percent",
                return_value=70,
            ),
        ):
            normalized = ai_director.validate_decision(decision)
        self.assertEqual(normalized.pulse_duration_ms, 1400)

    def test_hit_uses_percentage_command(self):
        decision = ai_director.Decision(
            action="hit",
            intensity_percent=35,
            scene="s",
            story="t",
            reason="r",
            save_snapshot=False,
        )

        with patch.object(
            ai_director.subprocess,
            "run",
        ) as run:
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            run.return_value.returncode = 0
            ai_director.execute_decision(decision, True)

        command = run.call_args.args[0]
        self.assertIn("--channel", command)
        self.assertEqual(command[command.index("--percent") + 1], "35")

    def test_ai_created_effect_is_bounded(self):
        decision = ai_director.Decision(
            action="hit", intensity_percent=40, scene="s", story="t",
            reason="r", save_snapshot=False,
            effect_steps=[
                {"relative_percent": 50, "duration_ms": 100,
                 "pattern": "PULSE"},
                {"relative_percent": 100, "duration_ms": 100,
                 "pattern": "RHYTHM"},
            ],
        )
        ai_director.validate_decision(decision)
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "current.json"
            with (
                patch.object(ai_director, "CURRENT_WAVEFORM_FILE", current),
                patch.object(ai_director.subprocess, "run") as run,
                patch.object(ai_director.time, "sleep"),
            ):
                run.return_value.stdout = ""
                run.return_value.stderr = ""
                run.return_value.returncode = 0
                ai_director.execute_decision(decision, True)
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertIn("waveform-control.py", command[1])
            self.assertIn("--background", command)
            self.assertTrue(current.exists())
            sequence = ai_director.json.loads(current.read_text(encoding="utf-8"))
            self.assertEqual(len(sequence["steps"]), 2)

    def test_ai_created_effect_rejects_excess_duration(self):
        decision = ai_director.Decision(
            action="hit", intensity_percent=20, scene="s", story="t",
            reason="r", save_snapshot=False,
            effect_steps=[
                {"relative_percent": 50, "duration_ms": 1000,
                 "pattern": "PULSE"}
                for _ in range(6)
            ],
        )
        with (
            patch.object(
                ai_director,
                "load_max_effect_duration_ms",
                return_value=5000,
            ),
            self.assertRaises(ValueError),
        ):
            ai_director.validate_decision(decision)

    def test_highlight_saves_image_and_metadata(self):
        decision = ai_director.Decision(
            action="none",
            intensity_percent=0,
            scene="s",
            story="climax",
            reason="r",
            save_snapshot=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                ai_director,
                "HIGHLIGHTS_DIR",
                Path(directory),
            ):
                target = ai_director.save_highlight(
                    TEST_IMAGE,
                    decision,
                )
            self.assertTrue(target.exists())
            self.assertTrue(target.with_suffix(".json").exists())


class LoopTests(unittest.TestCase):
    def test_hit_schedules_fast_reaction_observation(self):
        argv = [
            "ai-director.py", "--image", str(TEST_IMAGE), "--loop", "--execute",
            "--max-rounds", "2", "--loop-interval", "10",
        ]
        hit = ai_director.Decision(
            action="hit", intensity_percent=30, scene="s", story="t",
            reason="r", save_snapshot=False,
            feedback_mode="continuous_random",
        )
        none = ai_director.Decision(
            action="none", intensity_percent=0, scene="s2", story="t2",
            reason="r2", save_snapshot=False,
        )
        with (
            patch.object(sys, "argv", argv),
            patch.object(ai_director, "run_round", side_effect=[hit, none]),
            patch.object(ai_director, "ensure_fresh_frame"),
            patch.object(ai_director, "load_reaction_observation_seconds", return_value=2),
            patch.object(ai_director, "wait_for_new_frame", return_value=True) as wait_frame,
            patch.object(ai_director, "stop_device_after_loop"),
            patch.object(ai_director.time, "sleep") as sleep,
        ):
            result = ai_director.main()
        self.assertEqual(result, 0)
        self.assertEqual(wait_frame.call_args.args[2], 2)
        sleep.assert_not_called()

    def test_max_rounds_accepts_720(self):
        argv = [
            "ai-director.py", "--image", str(TEST_IMAGE),
            "--max-rounds", "720",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(ai_director, "run_round"),
            patch.object(ai_director, "ensure_fresh_frame"),
        ):
            result = ai_director.main()
        self.assertEqual(result, 0)

    def test_loop_stops_at_max_rounds(self):
        argv = [
            "ai-director.py",
            "--image",
            str(TEST_IMAGE),
            "--loop",
            "--max-rounds",
            "2",
            "--loop-interval",
            "10",
        ]

        with (
            patch.object(sys, "argv", argv),
            patch.object(
                ai_director,
                "run_round",
            ) as run_round,
            patch.object(
                ai_director,
                "ensure_fresh_frame",
            ),
            patch.object(ai_director.time, "sleep"),
        ):
            result = ai_director.main()

        self.assertEqual(result, 0)
        self.assertEqual(run_round.call_count, 2)


if __name__ == "__main__":
    unittest.main()
