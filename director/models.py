from __future__ import annotations

import json
from dataclasses import dataclass, field


class AuthenticationError(RuntimeError):
    """The configured provider rejected its API credential."""


@dataclass
class Decision:
    action: str
    intensity_percent: int
    scene: str
    story: str
    reason: str
    save_snapshot: bool
    channel: str = "A"
    duration_ms: int = 800
    pattern: str = "BUBBLE"
    effect_steps: list[dict] = field(default_factory=list)
    feedback_mode: str = "single"
    min_intensity_percent: int = 1
    interval_ms: int = 1000
    pulse_duration_ms: int = 800
    waveform_name: str = ""
    waveform_description: str = ""
    remember_waveform: bool = False
    feedback_sequence: list[dict] = field(default_factory=list)
    observed_reaction: str = ""
    target_armor: str = ""
    monster_action: str = ""
    weakness_hypothesis: str = ""

    @classmethod
    def from_json(cls, text: str) -> "Decision":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"AI 返回的内容不是有效 JSON: {error}") from error
        if not isinstance(data, dict):
            raise ValueError("AI 返回的 JSON 必须是对象")

        required = {
            "action", "intensity_percent", "scene", "story", "reason",
            "save_snapshot",
        }
        missing = required - data.keys()
        if missing:
            raise ValueError("AI 返回缺少字段: " + ", ".join(sorted(missing)))
        if data["action"] not in {"none", "hit", "stop"}:
            raise ValueError("action 必须是 none、hit 或 stop")
        if isinstance(data["intensity_percent"], bool) or not isinstance(data["intensity_percent"], int):
            raise ValueError("intensity_percent 必须是整数")
        if not isinstance(data["save_snapshot"], bool):
            raise ValueError("save_snapshot 必须是布尔值")

        channel = str(data.get("channel", "A")).upper()
        if channel not in {"A", "B", "AB"}:
            raise ValueError("channel 必须是 A、B 或 AB")
        duration_ms = data.get("duration_ms", 800)
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
            raise ValueError("duration_ms 必须是整数")
        pattern = str(data.get("pattern", "BUBBLE")).upper()
        effect_steps = data.get("effect_steps", [])
        if not isinstance(effect_steps, list):
            raise ValueError("effect_steps 必须是数组")
        feedback_mode = str(data.get("feedback_mode", "single")).lower()
        if feedback_mode not in {"single", "continuous_fixed", "continuous_random", "waveform"}:
            raise ValueError("feedback_mode 必须是 single、continuous_fixed、continuous_random 或 waveform")
        min_intensity_percent = data.get("min_intensity_percent", data["intensity_percent"])
        interval_ms = data.get("interval_ms", 1000)
        pulse_duration_ms = data.get("pulse_duration_ms", 800)
        for field_name, value in {
            "min_intensity_percent": min_intensity_percent,
            "interval_ms": interval_ms,
            "pulse_duration_ms": pulse_duration_ms,
        }.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} 必须是整数")
        waveform_name = str(data.get("waveform_name", ""))
        waveform_description = str(data.get("waveform_description", ""))
        remember_waveform = data.get("remember_waveform", False)
        feedback_sequence = data.get("feedback_sequence", [])
        if not isinstance(remember_waveform, bool):
            raise ValueError("remember_waveform 必须是布尔值")
        if not isinstance(feedback_sequence, list):
            raise ValueError("feedback_sequence 必须是数组")
        for field_name in (
            "scene", "story", "reason", "observed_reaction",
            "target_armor", "monster_action", "weakness_hypothesis",
        ):
            if field_name not in data:
                continue
            if not isinstance(data[field_name], str):
                raise ValueError(f"{field_name} 必须是字符串")

        return cls(
            action=data["action"], intensity_percent=data["intensity_percent"],
            scene=data["scene"], story=data["story"], reason=data["reason"],
            save_snapshot=data["save_snapshot"], channel=channel,
            duration_ms=duration_ms, pattern=pattern, effect_steps=effect_steps,
            feedback_mode=feedback_mode,
            min_intensity_percent=min_intensity_percent,
            interval_ms=interval_ms,
            pulse_duration_ms=pulse_duration_ms,
            waveform_name=waveform_name,
            waveform_description=waveform_description,
            remember_waveform=remember_waveform,
            feedback_sequence=feedback_sequence,
            observed_reaction=str(data.get("observed_reaction", ""))[:500],
            target_armor=str(data.get("target_armor", ""))[:200],
            monster_action=str(data.get("monster_action", ""))[:500],
            weakness_hypothesis=str(data.get("weakness_hypothesis", ""))[:500],
        )
