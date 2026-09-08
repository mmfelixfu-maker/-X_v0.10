from __future__ import annotations

import json
from pathlib import Path


DEFAULT_FEEDBACK = {
    "intensity_unit": "percent",
    "default_channel": "A",
    "max_duration_ms": 200000,
    "ai_response_timeout_seconds": 20,
    "reaction_observation_seconds": 2,
    "allowed_patterns": ["BUBBLE", "PULSE", "BREATHING", "GRADUALRUB", "RHYTHM"],
    "channels": {"A": {"enabled": True, "location": "未指定", "max_intensity": 10}},
}


def load_project_config(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise RuntimeError(f"找不到项目配置: {path}") from error
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"无法读取项目配置: {error}") from error
    if not isinstance(data, dict):
        raise RuntimeError("project-config.json 必须是 JSON 对象")
    feedback = data.get("feedback")
    if not isinstance(feedback, dict):
        raise RuntimeError("project-config.json 缺少 feedback 对象")
    if feedback.get("intensity_unit") != "percent":
        raise RuntimeError("feedback.intensity_unit 必须固定为 percent")
    return data


def session_preferences(path: Path) -> dict[str, str]:
    try:
        data = load_project_config(path)
    except RuntimeError:
        data = {}
    location = str(data.get("feedback_device_location", "未指定")).strip()[:200]
    style = str(data.get("game_style", "default")).strip()[:500]
    return {
        "feedback_device_location": location or "未指定",
        "game_style": style or "default",
        "story_pacing": str(data.get("story_pacing", "slow")).strip().lower(),
    }


def feedback_cycle(path: Path) -> tuple[int, int]:
    try:
        data = load_project_config(path)
        burst = int(data.get("feedback_burst_rounds", 3))
        rest = int(data.get("feedback_rest_rounds", 1))
    except (RuntimeError, ValueError, TypeError):
        return 3, 1
    return (burst, rest) if 1 <= burst <= 20 and 1 <= rest <= 10 else (3, 1)


def max_effect_duration_ms(path: Path) -> int:
    try:
        value = int(load_project_config(path)["feedback"].get("max_duration_ms", 200000))
    except (RuntimeError, ValueError, TypeError, KeyError):
        return 200000
    return max(100, min(600000, value))


def min_pulse_duty_percent(path: Path) -> int:
    try:
        value = int(
            load_project_config(path)["feedback"].get(
                "min_pulse_duty_percent", 70
            )
        )
    except (RuntimeError, ValueError, TypeError, KeyError):
        return 70
    return value if 10 <= value <= 100 else 70


def ai_response_timeout_seconds(path: Path) -> float:
    try:
        value = float(
            load_project_config(path)["feedback"].get(
                "ai_response_timeout_seconds", 20
            )
        )
    except (RuntimeError, ValueError, TypeError, KeyError):
        return 20.0
    return value if 3 <= value <= 120 else 20.0


def feedback_state_lease_ms(path: Path) -> int:
    """Keep a state alive through the interval and one bounded AI request."""
    try:
        data = load_project_config(path)
        interval = max(10.0, float(data.get("ai_interval_seconds", 10)))
    except (RuntimeError, ValueError, TypeError):
        interval = 10.0
    lease = round((interval + ai_response_timeout_seconds(path) + 2) * 1000)
    return min(max_effect_duration_ms(path), max(100, lease))


def reaction_observation_seconds(path: Path) -> float:
    try:
        value = float(
            load_project_config(path)["feedback"].get(
                "reaction_observation_seconds", 2
            )
        )
    except (RuntimeError, ValueError, TypeError, KeyError):
        return 2.0
    return value if 1 <= value <= 10 else 2.0
