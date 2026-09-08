from __future__ import annotations

import json
import re
from pathlib import Path


PATTERNS = {"BUBBLE", "PULSE", "BREATHING", "GRADUALRUB", "RHYTHM"}
MODES = {"fixed", "random", "rest"}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,48}$")


def validate_sequence(steps: object) -> list[dict]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
        raise ValueError("waveform steps 必须包含 1-16 段")
    normalized: list[dict] = []
    cycle_duration_ms = 0
    for index, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"waveform step {index} 必须是对象")
        mode = str(raw.get("mode", "")).lower()
        if mode not in MODES:
            raise ValueError(f"waveform step {index} mode 必须是 fixed、random 或 rest")
        duration_ms = raw.get("duration_ms")
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or not 100 <= duration_ms <= 600000:
            raise ValueError(f"waveform step {index} duration_ms 必须为 100-600000")
        cycle_duration_ms += duration_ms
        step = {"mode": mode, "duration_ms": duration_ms}
        if mode != "rest":
            interval_ms = raw.get("interval_ms", 1000)
            pulse_duration_ms = raw.get("pulse_duration_ms", min(800, interval_ms))
            pattern = str(raw.get("pattern", "BUBBLE")).upper()
            if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or not 100 <= interval_ms <= 600000:
                raise ValueError(f"waveform step {index} interval_ms 必须为 100-600000")
            if isinstance(pulse_duration_ms, bool) or not isinstance(pulse_duration_ms, int) or not 100 <= pulse_duration_ms <= min(60000, interval_ms):
                raise ValueError(f"waveform step {index} pulse_duration_ms 必须为 100 到 interval_ms")
            if pattern not in PATTERNS:
                raise ValueError(f"waveform step {index} pattern 不被允许")
            step.update({
                "interval_ms": interval_ms,
                "pulse_duration_ms": pulse_duration_ms,
                "pattern": pattern,
            })
            if mode == "fixed":
                relative = raw.get("relative_percent")
                if isinstance(relative, bool) or not isinstance(relative, int) or not 0 <= relative <= 100:
                    raise ValueError(f"waveform step {index} relative_percent 必须为 0-100")
                step["relative_percent"] = relative
            else:
                low = raw.get("min_relative_percent")
                high = raw.get("max_relative_percent")
                if any(isinstance(value, bool) or not isinstance(value, int) for value in (low, high)) or not 0 <= low <= high <= 100:
                    raise ValueError(f"waveform step {index} 随机范围必须满足 0 <= min <= max <= 100")
                step["min_relative_percent"] = low
                step["max_relative_percent"] = high
        normalized.append(step)
    if cycle_duration_ms > 600000:
        raise ValueError("单个波形循环不能超过 600000ms")
    return normalized


def validate_waveform(name: str, raw: object, source: str = "user") -> dict:
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError("波形名称只能包含字母、数字、下划线和连字符，最长 48 字符")
    if not isinstance(raw, dict):
        raise ValueError(f"波形 {name} 必须是对象")
    return {
        "description": str(raw.get("description", "")).strip()[:300],
        "source": str(raw.get("source", source)).strip()[:32] or source,
        "steps": validate_sequence(raw.get("steps")),
    }


def load_library(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {"version": 1, "waveforms": {}}
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取波形库: {error}") from error
    raw_waveforms = data.get("waveforms") if isinstance(data, dict) else None
    if not isinstance(raw_waveforms, dict):
        raise RuntimeError("波形库必须包含 waveforms 对象")
    if len(raw_waveforms) > 100:
        raise RuntimeError("波形库最多保存 100 个波形")
    return {
        "version": 1,
        "waveforms": {
            name: validate_waveform(name, waveform)
            for name, waveform in raw_waveforms.items()
        },
    }


def save_library(path: Path, library: dict) -> None:
    path.write_text(
        json.dumps(library, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def remember_waveform(path: Path, name: str, description: str, steps: list[dict], source: str = "ai") -> dict:
    library = load_library(path)
    waveform = validate_waveform(name, {
        "description": description,
        "source": source,
        "steps": steps,
    }, source=source)
    if name not in library["waveforms"] and len(library["waveforms"]) >= 100:
        raise ValueError("波形库已达到 100 个上限")
    library["waveforms"][name] = waveform
    save_library(path, library)
    return waveform
