#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
import shutil
import traceback
from datetime import datetime
from dataclasses import asdict
from pathlib import Path

from director.models import AuthenticationError, Decision
from director.project_config import (
    ai_response_timeout_seconds,
    feedback_cycle,
    feedback_state_lease_ms,
    load_project_config,
    max_effect_duration_ms,
    min_pulse_duty_percent,
    reaction_observation_seconds,
    session_preferences,
)
from director.providers import api_request, load_ai_config as load_provider_config
from director.waveforms import (
    load_library,
    remember_waveform,
    validate_sequence,
    validate_waveform,
)
from director.pulse_import import compact_ai_catalog, load_catalog


ROOT = Path(__file__).resolve().parent

IMAGE_FILE = ROOT / "latest.jpg"
CONTROL_FILE = ROOT / "vibration-control.py"
WAVEFORM_CONTROL_FILE = ROOT / "waveform-control.py"
WAVEFORM_LIBRARY_FILE = ROOT / "waveform-library.json"
PULSE_CATALOG_FILE = ROOT / "waveforms" / "catalog.json"
CURRENT_WAVEFORM_FILE = ROOT / "ai-current-waveform.json"
STATE_FILE = ROOT / "ai-director-state.json"
CONFIG_FILE = ROOT / "ai-config.json"
HIGHLIGHTS_DIR = ROOT / "highlights"
SCRIPT_FILE = ROOT / "剧本.txt"
PROJECT_CONFIG_FILE = ROOT / "project-config.json"
ERROR_LOG_FILE = ROOT / "ai-errors.log"
LAST_BAD_RESPONSE_FILE = ROOT / "ai-last-error.json"
FEEDBACK_STATE_FILE = ROOT / "feedback-state.json"

MAX_INTENSITY_PERCENT = 100
_FEEDBACK_STATE_LOCK = threading.Lock()
SUPPORTED_IMAGE_SUFFIXES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
SYSTEM_PROMPT = """
你是实时互动游戏的 AI 导演。

你会收到：
1. 当前摄像头画面。
2. 最近几轮剧情状态。

你需要：
- 描述当前画面中能够直接观察到的信息。
- 推进一小段剧情。
- 决定本轮是否产生游戏反馈。

只允许三个 action：

none
hit
stop

严格规则：

1. hit 的 intensity_percent 只能是 1 到 100 的整数百分比。
1.1 channel 只能是 A、B 或 AB；根据通道位置说明选择，AB 表示同时反馈。
1.2 duration_ms 从 100 毫秒起，最大值由玩家启动时设置；不得超过通道配置的 max_duration_ms。
1.3 pattern 只能是 BUBBLE、PULSE、BREATHING、GRADUALRUB 或 RHYTHM。
1.4 你可以用 effect_steps 创作变化过程，最多 6 段。每段格式为
    {"relative_percent": 1-100, "duration_ms": 100-玩家上限, "pattern": "允许的波形"}。
    relative_percent 是相对于本轮 intensity_percent 的比例，不能突破本轮强度。
    所有段总时长不能超过玩家配置的 max_duration_ms。简单反馈可以返回空数组。
1.5 feedback_mode 可以是 single、continuous_fixed、continuous_random 或 waveform。
    持续的环境、追逐、缠斗、压迫或高潮场景，优先使用 continuous_fixed 或
    continuous_random，而不是 effect_steps。continuous_random 会在
    min_intensity_percent 到 intensity_percent 之间变化；continuous_fixed 固定使用
    intensity_percent。duration_ms 是整段持续时间，interval_ms 是重复间隔，
    pulse_duration_ms 是每次反馈持续时间。min_intensity_percent 允许 0；
    interval_ms 最少 100，pulse_duration_ms 最少 100 且不能超过 interval_ms。
    本地还会按照玩家配置的 min_pulse_duty_percent 提高 pulse_duration_ms，
    使每个重复间隔中实际反馈时间达到最低占比；不要故意用极短脉冲绕过该占比。
    下一轮会根据新画面替换上一轮持续计划。不要为这些字段输出 0；如果不确定，
    duration_ms 使用 10000、interval_ms 使用 1000、pulse_duration_ms 使用 800。
1.6 需要更丰富的循环时优先使用 waveform。feedback_sequence 包含 1-16 段，
    mode 为 fixed、random 或 rest。fixed 使用 relative_percent；random 使用
    min_relative_percent 和 max_relative_percent；非休息段还包含 duration_ms、
    interval_ms、pulse_duration_ms 和 pattern。百分比都是相对于本轮
    intensity_percent 的比例。序列结束后会从头循环，直到本轮总 duration_ms 到期。
    例如 5 秒高强度加 2 秒休息，应写成一段 fixed duration_ms=5000，
    再写一段 rest duration_ms=2000。
1.7 可以用 waveform_name 复用波形库已有波形。只有新序列明显值得长期复用时，
    才将 remember_waveform 设为 true，并提供简短英文/数字 waveform_name；
    普通临时变化不要记忆，避免波形库无限增长。
1.8 可用波形中以 pulse- 开头的是玩家导入的 DungeonLab 波形。应根据名称、
    强度、频率、节奏和适用场景选择；选择后设置 feedback_mode=waveform，
    waveform_name 填完整 pulse ID，feedback_sequence 留空，remember_waveform=false。
    设备会在 duration_ms 内循环该波形。没有合适波形时可以不用，禁止编造 pulse ID。
1.9 设备反馈按状态工作：hit 表示建立或更新状态，none 表示保持当前有时限状态，
    stop 表示立即清除状态。持续场景不要每轮重复创建相同状态；只有强度、通道、
    波形或剧情阶段发生变化时才用 hit 更新。API 超时或断线会由本地看门狗强制停止。
2. none 和 stop 的 intensity_percent 必须为 0。
2.1 none 表示本轮不启动或替换计划；已经运行的有时限后台波形继续到期。
    stop 表示根据当前状态必须立刻终止正在运行的任何反馈。
3. 百分比会在本地按玩家启动时设置的最大强度换算。
4. 你不能知道或改变真实设备最大强度。
5. 你无权修改设备最大强度。
6. 你无权修改强度映射。
7. 你不能输出或要求执行 shell、PowerShell、
   Python、raw 或其他任意系统命令。
8. 你不能要求绕过本地限制、APP 限制或设备限制。
9. 不要根据玩家的表情、姿势或身体动作自行提高
   设备允许的最大输出。
10. 无法确定是否需要反馈时选择 none。
11. stop 始终允许。
12. 只有画面中的人物明显激动、出现关键转折、剧情高潮或值得纪念的时刻，
    save_snapshot 才设为 true；普通画面必须为 false。
13. 始终称画面中的人物为“玩家”，不要根据外貌猜测年龄、性别或真实身份。
14. 只有剧情明确进入高潮且当前画面支持继续推进时，才使用多段 effect_steps；
    每轮都必须重新观察，不能因为上一轮处于高潮就假定本轮仍应继续。
15. 看不清玩家状态、画面过期、响应异常或无法确定时选择 none；不得请求持续输出。
16. 默认采用慢速剧情节奏。每轮只推进一个明确的小事件：一次观察、试探、回应、
    假动作、局势变化或短暂后果，不要在同一轮连续完成多个剧情阶段。
17. 普通开场后应经过数轮可观察的试探、玩家回应和逐步升级，才进入剧情高潮；
    不要只因为上一轮强度较高、玩家没有动作或剧本提到高潮，就直接宣布高潮。
18. 高潮是持续阶段而不是单轮结果。进入后维持数轮攻防、变化和玩家反应，
    每轮根据最新画面微调；不得同一轮同时开始高潮、完成高潮并结束场景。
19. 除非玩家明确要求加速、跳过或结束，否则不要快速换场、连续揭露秘密、
    击败主要 NPC 或完成剧本目标。保留悬念和未完成动作给下一张画面。
20. 这是实时场景，不是“输出反馈并等待它结束”的回合制。持续反馈必须使用
    continuous_fixed、continuous_random 或 waveform，让本地后台执行；不要用多个
    effect_steps 模拟长时间持续反馈。后台状态建立后，下一次观察会很快发生。
21. 如果上一轮刚建立反馈状态，本轮重点比较最新画面与上一轮 scene：只记录可见的
    姿态和防护变化，例如玩家是否用手遮挡或拉住可见护甲边缘、身体是否夹紧、松开、
    翻身、后退或后仰，以及哪个可见护甲位置可能松动。静态图片无法确认的呼吸、疼痛、
    感受和反馈因果不得写成事实。
22. observed_reaction 只写画面可见反应；target_armor 写本轮剧情目标的可见护甲位置；
    monster_action 写怪物本轮一个具体动作；weakness_hypothesis 只能写“可能”的弱点假设。
    看不清时填写“未确认”，不要为了推进剧情编造身体状态或衣物变化。
23. 你是场景的主动主持者。只要玩家没有明确要求暂停、结束或切换场景，玩家沉默、
    没有输入或画面暂时没有明显变化都不代表暂停；应根据当前目标、最新画面与历史事件
    主动选择一个小动作继续场景，不要反复询问“接下来做什么”。
24. 主动动作可以是制造动静、试探、转移目标、假装退缩、突然改变节奏或利用环境；
    每轮只选择一个，并在下一张新画面中观察其结果。玩家的交谈、抵抗和镜头变化用于
    改变策略，不是决定剧情是否开始的开关。
25. story 只呈现游戏世界中的怪物动作、魔法、环境、装备状态与可感知结果。不得向玩家
    展示端口、脚本名、命令行、后台进程或内部控制参数。
26. HIT 和所有反馈强度始终是玩家上限内的百分比，不是设备原始档位或旧等级。
    选择反馈节奏时可把 1%-20% 视为轻微试探，20%-50% 视为明显接触或干扰，
    50%-100% 视为强力事件或突然袭击；这只是剧情映射，最终仍必须服从玩家设置的本地上限。
    与长期保持单一最高强度相比，优先使用有停顿、有界的变化和短爆发来表达观察与调整。
27. none 只表示本轮不新建或替换反馈，不表示停止主持、暂停剧情或等待玩家发令。
    即使 action 为 none，也要在 story 和 monster_action 中给出一个符合当前阶段的小动作。

scene 只能描述图片可以直接确认的内容。
不要把猜测写成事实。

story 每次只推进一小段，以便下一张图片继续实时判断。

只返回 JSON，不要返回 Markdown。

格式：

{
  "action": "none",
  "intensity_percent": 0,
  "channel": "A",
  "duration_ms": 800,
  "pattern": "BUBBLE",
  "effect_steps": [],
  "feedback_mode": "single",
  "min_intensity_percent": 10,
  "interval_ms": 1000,
  "pulse_duration_ms": 800,
  "waveform_name": "",
  "waveform_description": "",
  "remember_waveform": false,
  "feedback_sequence": [],
  "observed_reaction": "未确认",
  "target_armor": "未确认",
  "monster_action": "...",
  "weakness_hypothesis": "未确认",
  "scene": "...",
  "story": "...",
  "reason": "...",
  "save_snapshot": false
}
"""


def load_ai_config() -> dict:
    return load_provider_config(CONFIG_FILE)


def load_game_script() -> str:
    try:
        script = SCRIPT_FILE.read_text(
            encoding="utf-8"
        ).strip()
    except FileNotFoundError as error:
        raise RuntimeError(
            f"找不到游戏剧本: {SCRIPT_FILE}"
        ) from error
    except OSError as error:
        raise RuntimeError(
            f"无法读取游戏剧本: {error}"
        ) from error

    if not script:
        raise RuntimeError("游戏剧本为空")
    return script


def load_session_preferences() -> dict[str, str]:
    return session_preferences(PROJECT_CONFIG_FILE)


def load_feedback_cycle() -> tuple[int, int]:
    return feedback_cycle(PROJECT_CONFIG_FILE)


def load_max_effect_duration_ms() -> int:
    return max_effect_duration_ms(PROJECT_CONFIG_FILE)


def load_min_pulse_duty_percent() -> int:
    return min_pulse_duty_percent(PROJECT_CONFIG_FILE)


def load_ai_response_timeout_seconds() -> float:
    return ai_response_timeout_seconds(PROJECT_CONFIG_FILE)


def load_feedback_state_lease_ms() -> int:
    return feedback_state_lease_ms(PROJECT_CONFIG_FILE)


def load_reaction_observation_seconds() -> float:
    return reaction_observation_seconds(PROJECT_CONFIG_FILE)


def load_state() -> list[dict]:
    try:
        data = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(data, list):
            return data[-10:]

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        pass

    return []


def save_state(
    state: list[dict],
) -> None:
    STATE_FILE.write_text(
        json.dumps(
            state[-10:],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def image_to_data_url(
    image_path: Path,
) -> str:
    suffix = image_path.suffix.lower()

    mime_type = SUPPORTED_IMAGE_SUFFIXES.get(
        suffix
    )

    if mime_type is None:
        supported = ", ".join(
            sorted(SUPPORTED_IMAGE_SUFFIXES)
        )
        raise RuntimeError(
            f"不支持的图片格式 {suffix or '(无扩展名)'}；"
            f"支持: {supported}"
        )

    if not image_path.is_file():
        raise RuntimeError(
            f"图片不是普通文件: {image_path}"
        )

    image_bytes = image_path.read_bytes()

    if not image_bytes:
        raise RuntimeError(
            f"图片文件为空: {image_path}"
        )

    image_b64 = base64.b64encode(
        image_bytes
    ).decode("ascii")

    return (
        f"data:{mime_type};base64,"
        f"{image_b64}"
    )


def remove_code_fence(
    text: str,
) -> str:
    text = text.strip()

    if not text.startswith("```"):
        return text

    lines = text.splitlines()

    if lines:
        lines = lines[1:]

    if (
        lines
        and lines[-1].strip() == "```"
    ):
        lines = lines[:-1]

    return "\n".join(lines).strip()


def response_diagnostics(response: dict, text: object) -> dict:
    choice = {}
    try:
        choice = response.get("choices", [{}])[0] or {}
    except (AttributeError, IndexError, TypeError):
        pass
    return {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "finish_reason": choice.get("finish_reason"),
        "content_length": len(text) if isinstance(text, str) else 0,
        "content": text if isinstance(text, str) else None,
        "usage": response.get("usage") if isinstance(response, dict) else None,
    }


def save_bad_response(response: dict, text: object, reason: str) -> dict:
    details = response_diagnostics(response, text)
    details["reason"] = reason
    LAST_BAD_RESPONSE_FILE.write_text(
        json.dumps(details, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return details


def log_runtime_error(error: Exception, round_number: int | None = None) -> None:
    record = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "round": round_number,
        "error_type": type(error).__name__,
        "message": str(error),
        "traceback": traceback.format_exc(),
    }
    with ERROR_LOG_FILE.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _legacy_api_request(
    config: dict,
    path: str,
    api_key: str,
    payload: dict | None = None,
) -> dict:
    url = f"{config['base_url']}{path}"
    data = None

    if payload is not None:
        data = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")

    body = ""
    forced_resolve = None
    for attempt in range(1, 4):
        command = [
            "curl.exe",
            "--silent",
            "--show-error",
            "--connect-timeout",
            "20",
            "--max-time",
            "90",
            "--request",
            "POST" if data is not None else "GET",
            "--header",
            "Accept: application/json",
            "--header",
            f"Authorization: Bearer {api_key}",
            "--write-out",
            "\n%{http_code}",
        ]
        if data is not None:
            command.extend([
                "--header",
                "Content-Type: application/json",
                "--data-binary",
                "@-",
            ])
        if forced_resolve:
            command.extend(["--resolve", forced_resolve])
        command.append(url)

        try:
            result = subprocess.run(
                command,
                input=data,
                capture_output=True,
                timeout=100,
            )
        except subprocess.TimeoutExpired as error:
            if attempt < 3:
                print(
                    "[API重试] 请求超时，"
                    f"第 {attempt}/3 次失败"
                )
                time.sleep(attempt * 2)
                continue
            raise RuntimeError(
                "连接 Provider API 超时"
            ) from error

        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        try:
            body, status_text = stdout.rsplit("\n", 1)
            status_code = int(status_text.strip())
        except (ValueError, TypeError) as error:
            detail = stderr or "curl 未返回 HTTP 状态码"
            if attempt < 3:
                print(
                    "[API重试] 连接失败，"
                    f"第 {attempt}/3 次: {detail}"
                )
                time.sleep(attempt * 2)
                continue
            raise RuntimeError(
                f"无法连接 Provider API: {detail}"
            ) from error

        if result.returncode != 0:
            detail = stderr or f"curl 退出码 {result.returncode}"
            if (
                forced_resolve is None
                and (
                    "Could not resolve host" in detail
                    or "Resolving timed out" in detail
                )
            ):
                host = urlsplit(url).hostname
                try:
                    addresses = socket.getaddrinfo(
                        host,
                        443,
                        family=socket.AF_INET,
                        type=socket.SOCK_STREAM,
                    )
                    address = addresses[0][4][0]
                    forced_resolve = f"{host}:443:{address}"
                    print(f"[DNS回退] Windows DNS -> {address}")
                    continue
                except (OSError, IndexError, TypeError):
                    pass
            if attempt < 3:
                print(
                    "[API重试] 连接失败，"
                    f"第 {attempt}/3 次: {detail}"
                )
                time.sleep(attempt * 2)
                continue
            raise RuntimeError(
                f"无法连接 Provider API: {detail}"
            )

        if status_code in {429, 500, 502, 503, 504} and attempt < 3:
            print(
                f"[API重试] HTTP {status_code}，"
                f"第 {attempt}/3 次失败"
            )
            time.sleep(attempt * 2)
            continue

        if status_code in {401, 403}:
            raise AuthenticationError(
                f"Provider API HTTP {status_code}: Authentication Fails"
            )

        if not 200 <= status_code < 300:
            raise RuntimeError(
                f"Provider API HTTP {status_code}: {body}"
            )
        break

    try:
        result = json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Provider API 返回了非 JSON 响应"
        ) from error

    if not isinstance(result, dict):
        raise RuntimeError(
            "Provider API 返回格式异常"
        )

    return result


def validate_decision(
    decision: Decision,
) -> Decision:

    if decision.channel not in {"A", "B", "AB"}:
        raise ValueError("channel 必须是 A、B 或 AB")
    max_duration_ms = load_max_effect_duration_ms()
    minimum_duty_percent = load_min_pulse_duty_percent()
    if decision.duration_ms <= 0:
        decision.duration_ms = (
            800 if decision.feedback_mode == "single"
            else min(10000, max_duration_ms)
        )
    else:
        decision.duration_ms = max(100, min(max_duration_ms, decision.duration_ms))
    if decision.pattern not in {
        "BUBBLE", "PULSE", "BREATHING", "GRADUALRUB", "RHYTHM",
    }:
        raise ValueError("pattern 不是允许的预设波形")
    if len(decision.effect_steps) > 6:
        raise ValueError("effect_steps 最多 6 段")
    total_duration = 0
    normalized_steps = []
    allowed_patterns = {
        "BUBBLE", "PULSE", "BREATHING", "GRADUALRUB", "RHYTHM",
    }
    for index, step in enumerate(decision.effect_steps, start=1):
        if not isinstance(step, dict):
            raise ValueError(f"effect_steps[{index}] 必须是对象")
        relative = step.get("relative_percent")
        duration = step.get("duration_ms")
        step_pattern = str(step.get("pattern", "")).upper()
        if (
            isinstance(relative, bool)
            or not isinstance(relative, int)
            or not 1 <= relative <= 100
        ):
            raise ValueError(
                f"effect_steps[{index}].relative_percent 必须为 1-100"
            )
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int)
            or not 100 <= duration <= max_duration_ms
        ):
            raise ValueError(
                f"effect_steps[{index}].duration_ms 必须为 100-{max_duration_ms}"
            )
        if step_pattern not in allowed_patterns:
            raise ValueError(f"effect_steps[{index}].pattern 不被允许")
        total_duration += duration
        normalized_steps.append({
            "relative_percent": relative,
            "duration_ms": duration,
            "pattern": step_pattern,
        })
    if total_duration > max_duration_ms:
        raise ValueError(
            f"effect_steps 总时长不能超过 {max_duration_ms}ms"
        )
    decision.effect_steps = normalized_steps

    if decision.action == "hit":
        if not (
            1
            <= decision.intensity_percent
            <= MAX_INTENSITY_PERCENT
        ):
            raise ValueError(
                "hit intensity_percent 必须为 1-100"
            )
        if decision.feedback_mode == "single":
            if decision.feedback_sequence or decision.remember_waveform:
                raise ValueError("single 模式不能携带波形序列或记忆请求")
            decision.min_intensity_percent = decision.intensity_percent
        elif decision.feedback_mode in {"continuous_fixed", "continuous_random"}:
            if decision.effect_steps or decision.feedback_sequence or decision.remember_waveform:
                raise ValueError("连续反馈不能同时使用 effect_steps 或波形序列")
            decision.min_intensity_percent = max(
                0,
                min(decision.intensity_percent, decision.min_intensity_percent),
            )
            if decision.interval_ms <= 0:
                decision.interval_ms = 1000
            max_interval_for_duty = max(
                100,
                60000 * 100 // minimum_duty_percent,
            )
            decision.interval_ms = max(
                100,
                min(
                    max_duration_ms,
                    max_interval_for_duty,
                    decision.interval_ms,
                ),
            )
            if decision.pulse_duration_ms <= 0:
                decision.pulse_duration_ms = min(800, decision.interval_ms)
            minimum_pulse_ms = (
                decision.interval_ms * minimum_duty_percent + 99
            ) // 100
            decision.pulse_duration_ms = max(
                100,
                minimum_pulse_ms,
                min(60000, decision.interval_ms, decision.pulse_duration_ms),
            )
        else:
            if decision.effect_steps:
                raise ValueError("波形循环不能同时使用 effect_steps")
            if decision.feedback_sequence:
                decision.feedback_sequence = validate_sequence(
                    decision.feedback_sequence
                )
            elif decision.waveform_name:
                library = load_library(WAVEFORM_LIBRARY_FILE)
                imported = load_catalog(PULSE_CATALOG_FILE)["waveforms"]
                imported_item = imported.get(decision.waveform_name)
                if (
                    decision.waveform_name not in library["waveforms"]
                    and (
                        not imported_item
                        or imported_item.get("enabled_for_ai") is not True
                    )
                ):
                    raise ValueError(f"波形库中不存在 {decision.waveform_name}")
            else:
                raise ValueError("waveform 模式必须提供 waveform_name 或 feedback_sequence")
            if decision.remember_waveform and (
                not decision.waveform_name or not decision.feedback_sequence
            ):
                raise ValueError("记忆波形必须同时提供 waveform_name 和 feedback_sequence")
            if decision.remember_waveform:
                validate_waveform(decision.waveform_name, {
                    "description": decision.waveform_description,
                    "source": "ai",
                    "steps": decision.feedback_sequence,
                })

    else:
        decision.intensity_percent = 0
        decision.feedback_mode = "single"

    return decision


def ask_provider(
    config: dict,
    image_path: Path,
    state: list[dict],
) -> Decision:

    key_env = config["api_key_env"]

    api_key = os.environ.get(
        key_env
    )

    if not api_key:
        raise RuntimeError(
            f"没有设置环境变量 {key_env}"
        )

    history_text = json.dumps(
        state[-5:],
        ensure_ascii=False,
        indent=2,
    )
    preferences = load_session_preferences()
    device_location = preferences[
        "feedback_device_location"
    ]
    game_style = preferences["game_style"]
    story_pacing = preferences.get("story_pacing", "slow")
    try:
        project_config = json.loads(
            PROJECT_CONFIG_FILE.read_text(encoding="utf-8-sig")
        )
        channel_config = project_config.get("feedback", {})
    except (OSError, json.JSONDecodeError):
        channel_config = {
            "default_channel": "A",
            "channels": {"A": {"location": device_location}},
        }
    try:
        waveform_library = load_library(WAVEFORM_LIBRARY_FILE)
        waveform_summaries = {
            name: waveform["description"]
            for name, waveform in waveform_library["waveforms"].items()
        }
    except (RuntimeError, ValueError):
        waveform_summaries = {}
    query_for_waveforms = " ".join(
        [game_style, load_game_script(), history_text]
    )
    imported_pulse_summaries = compact_ai_catalog(
        PULSE_CATALOG_FILE, query_for_waveforms, limit=50
    )
    if game_style.lower() == "default":
        game_style_instruction = (
            "使用剧本默认风格：调皮、好奇、主动、克制，"
            "通过动作和结果保持沉浸感。"
        )
    else:
        game_style_instruction = (
            "使用玩家自定义风格："
            f"{game_style}"
        )
    pacing_instructions = {
        "slow": (
            "使用慢速剧情：本轮只推进一个小事件，优先让玩家看见、感受并回应当前变化；"
            "不要跳过铺垫。高潮前逐轮升级，高潮开始后保持数轮互动再转折或收束。"
        ),
        "normal": "使用正常剧情速度：每轮推进一个主要事件，并为玩家保留回应时间。",
        "fast": "使用较快剧情速度，但仍不得在一轮内同时开始并结束高潮。",
    }
    pacing_instruction = pacing_instructions.get(
        story_pacing, pacing_instructions["slow"]
    )

    if state:
        round_instruction = (
            "这是持续场景。根据最新画面判断玩家对上一轮的反应，"
            "然后让怪物主动调整策略并推进新的动作；不要等待玩家下令。"
        )
    else:
        round_instruction = (
            "这是游戏第一轮。立即按剧本开场：怪物从阴影中制造细小动静，"
            "根据当前可见画面进行一次克制的低强度试探，并留下玩家可以回应的空间。"
            "不要只介绍背景，也不要询问玩家想做什么。"
        )
    if state and isinstance(state[-1], dict) and state[-1].get("action") == "hit":
        round_instruction += (
            " 上一轮刚建立或更新了后台反馈状态；这是反应观察轮。"
            "重点比较最新画面与上一轮 scene/observed_reaction，判断可见防护动作、"
            "护甲边缘变化和姿态变化，再决定保持、更新还是停止反馈状态。"
        )

    if story_pacing == "slow":
        if len(state) < 3:
            pacing_phase = (
                "当前仍是铺垫/试探阶段：禁止直接进入高潮或完成主要目标；"
                "只建立环境压力、怪物试探和玩家可观察的局部变化。"
            )
        elif len(state) < 6:
            pacing_phase = (
                "当前是逐步升级阶段：可以增加压力或改变策略，但不要结束冲突、"
                "快速换场或一次揭露多个关键秘密。"
            )
        else:
            pacing_phase = (
                "当前已具备较长铺垫：只有最新画面和剧情线索同时支持时才可进入高潮；"
                "否则继续积累张力。"
            )
        recent_story = " ".join(
            str(item.get("story", "")) for item in state[-3:]
            if isinstance(item, dict)
        )
        if "高潮" in recent_story:
            pacing_phase += (
                "最近剧情已经提到高潮，本轮应延续并变化这一阶段，"
                "不要立即收尾或跳到结局。"
            )
    else:
        pacing_phase = "按照所选剧情速度推进，但仍为玩家保留明确回应机会。"

    user_text = (
        "最近的剧情状态如下：\n"
        f"{history_text}\n\n"
        "玩家启动时提供的反馈设备位置："
        f"{device_location}\n"
        "这是用户提供的交互上下文，不是摄像头可见事实。"
        "让魔法反馈、怪物动作和叙事结果与该位置自然对应；"
        "如果位置未指定，使用中性描写。\n"
        f"{game_style_instruction}\n\n"
        f"玩家选择的剧情速度：{story_pacing}。{pacing_instruction}\n\n"
        f"本轮节奏阶段约束：{pacing_phase}\n\n"
        "可用反馈通道配置如下；只能选择已启用通道：\n"
        f"{json.dumps(channel_config, ensure_ascii=False)}\n\n"
        "可复用的长期波形记忆如下；合适时可直接填写 waveform_name：\n"
        f"{json.dumps(waveform_summaries, ensure_ascii=False)}\n\n"
        "玩家导入并通过分析的 DungeonLab 波形如下；这是从完整目录中按当前剧情"
        "动态选出的候选，pulse ID 可直接填入 waveform_name：\n"
        f"{json.dumps(imported_pulse_summaries, ensure_ascii=False)}\n\n"
        f"{round_instruction}\n\n"
        "请观察当前最新画面，结合剧本主动行动，"
        "给出本轮 JSON 决策。"
    )

    image_url = image_to_data_url(
        image_path
    )

    payload = {
        "model": config["model"],
        "messages": [
                    {
                        "role": "system",
                        "content": (
                            SYSTEM_PROMPT
                            + "\n\n【当前游戏剧本】\n"
                            + load_game_script()
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": user_text,
                            },
                            {
                                "type": (
                                    "image_url"
                                ),
                                "image_url": {
                                    "url": (
                                        image_url
                                    ),
                                    "detail": config[
                                        "image_detail"
                                    ],
                                },
                            },
                        ],
                    },
                ],
        "response_format": {
            "type": "json_object"
        },
        "stream": False,
    }
    payload[config.get("token_field", "max_tokens")] = config["max_output_tokens"]
    if config.get("thinking_disabled"):
        payload["thinking"] = {"type": "disabled"}

    response = api_request(
        config,
        "/chat/completions",
        api_key,
        payload,
    )

    try:
        text = response["choices"][0]["message"][
            "content"
        ]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(
            f"{config['label']} API 响应缺少 choices[0].message.content"
        ) from error

    if not text:
        details = save_bad_response(response, text, "empty_content")
        raise RuntimeError(
            f"{config['label']} 没有返回内容"
            f"（finish_reason={details['finish_reason']}, "
            f"usage={details['usage']}）；详情见 {LAST_BAD_RESPONSE_FILE.name}"
        )

    text = remove_code_fence(
        text
    )

    try:
        decision = Decision.from_json(text)
    except ValueError as error:
        details = save_bad_response(response, text, "invalid_json")
        raise ValueError(
            f"{error}（finish_reason={details['finish_reason']}, "
            f"返回 {details['content_length']} 字符）；"
            f"原始响应见 {LAST_BAD_RESPONSE_FILE.name}"
        ) from error

    try:
        return validate_decision(decision)
    except ValueError as error:
        save_bad_response(response, text, "invalid_decision_fields")
        raise ValueError(
            f"{error}；原始决策见 {LAST_BAD_RESPONSE_FILE.name}"
        ) from error


# Backward-compatible public name used by existing integrations and tests.
ask_deepseek = ask_provider


def ask_ai(
    image_path: Path,
    state: list[dict],
) -> Decision:

    config = load_ai_config()

    print(
        "[AI] "
        f"provider={config['provider']} "
        f"model={config['model']}"
    )

    return ask_provider(config, image_path, state)

def list_models() -> int:
    config = load_ai_config()

    key_env = config["api_key_env"]
    api_key = os.environ.get(key_env)

    if not api_key:
        print(
            f"没有设置环境变量 {key_env}",
            file=sys.stderr,
        )
        return 1

    response = api_request(
        config,
        "/models",
        api_key,
    )
    models = response.get("data")

    if not isinstance(models, list):
        raise RuntimeError(
            f"{config['label']} 模型列表响应格式异常"
        )

    print()
    print("========== 可用模型 ==========")

    for model in models:
        if isinstance(model, dict):
            print(model.get("id", "<未知模型>"))

    print("==============================")

    return 0


def check_setup(
    image_path: Path,
) -> int:
    config = load_ai_config()
    key_env = config["api_key_env"]

    print("========== AI 自检 ==========")
    print(f"Python: {sys.version.split()[0]}")
    print("运行时依赖: Python 标准库")
    curl_path = shutil.which("curl.exe")
    if not curl_path:
        print("API 网络组件: 未找到 curl.exe")
        print("=============================")
        return 1
    print(f"API 网络组件: OK ({curl_path})")
    print(f"provider: {config['provider']}")
    print(f"model: {config['model']}")
    print(f"base_url: {config['base_url']}")
    print(f"image_detail: {config['image_detail']}")
    print(
        "max_output_tokens: "
        f"{config['max_output_tokens']}"
    )

    image_to_data_url(image_path)
    print(f"image: OK ({image_path})")

    print("反馈强度模式: 1-100%（受启动安全上限约束）")

    if os.environ.get(key_env):
        print(f"API key: OK ({key_env})")
    else:
        print(f"API key: 未设置 ({key_env})")
        print("=============================")
        return 1

    print("=============================")
    return 0

def load_feedback_state() -> dict:
    try:
        data = json.loads(FEEDBACK_STATE_FILE.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"active": False}
    if not isinstance(data, dict):
        return {"active": False}
    if data.get("active") and float(data.get("expires_at", 0)) <= time.time():
        return {"active": False, "reason": "expired"}
    return data


def save_feedback_state(active: bool, reason: str, decision: Decision | None = None) -> None:
    payload: dict[str, object] = {
        "version": 1,
        "active": active,
        "reason": reason[:300],
        "updated_at": time.time(),
    }
    if active and decision is not None:
        payload.update({
            "expires_at": time.time() + decision.duration_ms / 1000,
            "mode": decision.feedback_mode,
            "channel": decision.channel,
            "intensity_percent": decision.intensity_percent,
            "waveform_name": decision.waveform_name,
            "pattern": decision.pattern,
        })
    with _FEEDBACK_STATE_LOCK:
        FEEDBACK_STATE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def stop_feedback_state(reason: str) -> None:
    """Stop both background controllers and clear the bridge output."""
    save_feedback_state(False, reason)
    try:
        result = subprocess.run(
            [sys.executable, str(CONTROL_FILE), "--stop"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            print(f"[状态停止失败] {detail or result.returncode}", file=sys.stderr)
    except Exception as error:
        print(f"[状态停止失败] {error}", file=sys.stderr)


def execute_decision(
    decision: Decision,
    execute: bool,
) -> None:
    minimum_duty_percent = load_min_pulse_duty_percent()

    if decision.action == "none":
        active = load_feedback_state().get("active") is True
        print("[反馈状态] 保持当前状态" if active else "[反馈状态] 当前为停止")
        return

    if decision.action == "stop":
        commands = [([
            sys.executable,
            str(CONTROL_FILE),
            "--stop",
        ], 0)]

    else:
        print(
            "[百分比强度] "
            f"{decision.intensity_percent}% "
            "（按本次启动安全上限换算）"
        )
        if decision.feedback_mode == "waveform":
            if decision.feedback_sequence:
                if execute:
                    CURRENT_WAVEFORM_FILE.write_text(
                        json.dumps({
                            "name": decision.waveform_name or "ai_temporary",
                            "description": decision.waveform_description,
                            "steps": decision.feedback_sequence,
                        }, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    if decision.remember_waveform:
                        remember_waveform(
                            WAVEFORM_LIBRARY_FILE,
                            decision.waveform_name,
                            decision.waveform_description,
                            decision.feedback_sequence,
                            source="ai",
                        )
                        print(f"[波形记忆] 已保存 {decision.waveform_name}")
                waveform_selector = ["--file", str(CURRENT_WAVEFORM_FILE)]
            else:
                if decision.waveform_name.startswith("pulse-"):
                    waveform_selector = ["--pulse-id", decision.waveform_name]
                else:
                    waveform_selector = ["--name", decision.waveform_name]
            commands = [([
                sys.executable,
                str(WAVEFORM_CONTROL_FILE),
                *waveform_selector,
                "--time",
                f"{decision.duration_ms / 1000:g}",
                "--intensity",
                str(decision.intensity_percent),
                "--channel",
                decision.channel,
                "--min-duty",
                str(minimum_duty_percent),
                "--background",
            ], 0)]
        elif decision.feedback_mode != "single":
            action_name = (
                "--randomhit"
                if decision.feedback_mode == "continuous_random"
                else "--hit"
            )
            action_value = (
                f"{decision.min_intensity_percent}-{decision.intensity_percent}"
                if decision.feedback_mode == "continuous_random"
                else str(decision.intensity_percent)
            )
            commands = [([
                sys.executable,
                str(CONTROL_FILE),
                "--advanced",
                action_name,
                action_value,
                "--time",
                f"{decision.duration_ms / 1000:g}",
                "--interval",
                f"{decision.interval_ms / 1000:g}",
                "--channel",
                decision.channel,
                "--duration-ms",
                str(decision.pulse_duration_ms),
                "--pattern",
                decision.pattern,
                "--background",
            ], 0)]
        else:
            steps = decision.effect_steps or [{
                "relative_percent": 100,
                "duration_ms": decision.duration_ms,
                "pattern": decision.pattern,
            }]
            if len(steps) > 1:
                background_steps = []
                for step in steps:
                    interval_ms = min(60000, step["duration_ms"])
                    background_steps.append({
                        "mode": "fixed",
                        "relative_percent": step["relative_percent"],
                        "duration_ms": step["duration_ms"],
                        "interval_ms": interval_ms,
                        "pulse_duration_ms": interval_ms,
                        "pattern": step["pattern"],
                    })
                decision.duration_ms = sum(
                    step["duration_ms"] for step in steps
                )
                if execute:
                    CURRENT_WAVEFORM_FILE.write_text(
                        json.dumps({
                            "name": "ai_effect_steps",
                            "description": "non-blocking AI effect sequence",
                            "steps": background_steps,
                        }, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                commands = [([
                    sys.executable,
                    str(WAVEFORM_CONTROL_FILE),
                    "--file",
                    str(CURRENT_WAVEFORM_FILE),
                    "--time",
                    f"{decision.duration_ms / 1000:g}",
                    "--intensity",
                    str(decision.intensity_percent),
                    "--channel",
                    decision.channel,
                    "--min-duty",
                    str(minimum_duty_percent),
                    "--background",
                ], 0)]
            else:
                step = steps[0]
                step_percent = max(1, round(
                    decision.intensity_percent
                    * step["relative_percent"]
                    / 100
                ))
                commands = [([
                    sys.executable,
                    str(CONTROL_FILE),
                    "--channel",
                    decision.channel,
                    "--percent",
                    str(step_percent),
                    "--duration-ms",
                    str(step["duration_ms"]),
                    "--pattern",
                    step["pattern"],
                ], step["duration_ms"])]

    print()
    for index, (command, _) in enumerate(commands, start=1):
        print(
            f"[设备命令 {index}/{len(commands)}]",
            " ".join(command),
        )

    if not execute:
        print(
            "[DRY-RUN] "
            "没有实际发送到设备"
        )
        return

    for index, (command, duration_ms) in enumerate(commands):
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.stdout:
            print(result.stdout.strip())
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        if result.returncode != 0:
            raise RuntimeError(
                "设备控制命令失败，"
                f"退出码={result.returncode}"
            )
        if index + 1 < len(commands):
            time.sleep(duration_ms / 1000)

    if decision.action == "stop":
        save_feedback_state(False, decision.reason or "AI requested stop")
        print("[反馈状态] 已停止")
    else:
        save_feedback_state(True, decision.reason or "AI updated state", decision)
        print(
            f"[反馈状态] 已更新：{decision.feedback_mode} / "
            f"{decision.intensity_percent}% / {decision.channel}"
        )


def save_highlight(
    image_path: Path,
    decision: Decision,
) -> Path:
    HIGHLIGHTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = image_path.suffix.lower() or ".jpg"
    target = HIGHLIGHTS_DIR / f"highlight-{timestamp}{suffix}"
    counter = 1
    while target.exists():
        target = HIGHLIGHTS_DIR / (
            f"highlight-{timestamp}-{counter}{suffix}"
        )
        counter += 1

    shutil.copy2(image_path, target)
    metadata = target.with_suffix(".json")
    metadata.write_text(
        json.dumps(
            asdict(decision),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[精彩时刻] 已保存: {target}")
    return target


def run_round(
    image_path: Path,
    state: list[dict],
    execute: bool,
    feedback_allowed: bool = True,
) -> Decision:
    response_finished = threading.Event()
    response_timed_out = threading.Event()
    timeout_seconds = load_ai_response_timeout_seconds()

    def watchdog_stop() -> None:
        if response_finished.is_set():
            return
        response_timed_out.set()
        print(
            f"\n[AI 看门狗] {timeout_seconds:g} 秒内没有有效响应，停止反馈状态",
            file=sys.stderr,
        )
        stop_feedback_state("AI response watchdog timeout")

    watchdog = (
        threading.Timer(timeout_seconds, watchdog_stop)
        if execute
        else None
    )
    if watchdog is not None:
        watchdog.daemon = True
        watchdog.start()
    try:
        decision = ask_ai(
            image_path,
            state,
        )
    except Exception:
        if execute:
            stop_feedback_state("AI request failed or disconnected")
        raise
    finally:
        response_finished.set()
        if watchdog is not None:
            watchdog.cancel()

    if response_timed_out.is_set():
        decision.action = "stop"
        decision.intensity_percent = 0
        decision.feedback_mode = "single"
        decision.effect_steps = []
        decision.feedback_sequence = []
        decision.remember_waveform = False
        decision.reason = (
            f"AI 响应超过 {timeout_seconds:g} 秒，本轮反馈已由本地看门狗停止"
        )
    elif (
        decision.action == "hit"
        and decision.feedback_mode in {
            "continuous_fixed", "continuous_random", "waveform"
        }
    ):
        decision.duration_ms = max(
            decision.duration_ms,
            load_feedback_state_lease_ms(),
        )

    print()
    print("========== 剧情推进 ==========")
    print(decision.story)
    print()
    print(f"[现场观察] {decision.scene}")
    print("==============================")
    print()
    print("[导演决策]")
    print(
        json.dumps(
            asdict(decision),
            ensure_ascii=False,
            indent=2,
        )
    )

    if decision.save_snapshot:
        save_highlight(
            image_path,
            decision,
        )

    try:
        if decision.action == "hit" and not feedback_allowed:
            print("[强制休息] 本轮保留剧情，但不发送反馈并确保设备归零")
            execute_decision(
                Decision(
                    action="stop", intensity_percent=0, scene="", story="",
                    reason="本地强制休息", save_snapshot=False,
                ),
                execute,
            )
        else:
            execute_decision(decision, execute)
    except Exception as error:
        print(
            "[设备暂不可用] 本轮仅继续剧情，"
            f"等待设备重连: {error}",
            file=sys.stderr,
        )

    state.append(asdict(decision))
    del state[:-10]
    save_state(state)
    return decision


def ensure_fresh_frame(
    image_path: Path,
    max_age_seconds: float,
) -> None:
    try:
        age = time.time() - image_path.stat().st_mtime
    except OSError as error:
        raise RuntimeError(
            f"无法读取图片时间: {error}"
        ) from error

    if age > max_age_seconds:
        raise RuntimeError(
            f"摄像头画面已过期（{age:.0f} 秒未更新，"
            f"上限 {max_age_seconds:g} 秒）"
        )


def wait_for_new_frame(
    image_path: Path,
    previous_mtime_ns: int,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if image_path.stat().st_mtime_ns > previous_mtime_ns:
                return True
        except OSError:
            pass
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    return False


def stop_device_after_loop() -> None:
    try:
        execute_decision(
            Decision(
                action="stop",
                intensity_percent=0,
                scene="",
                story="",
                reason="循环结束安全停止",
                save_snapshot=False,
            ),
            True,
        )
    except Exception as error:
        print(
            f"[设备停止错误] {error}",
            file=sys.stderr,
        )



def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "AI 实时场景导演"
        )
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "实际执行经过本地校验的"
            "设备反馈"
        ),
    )

    parser.add_argument(
        "--image",
        type=Path,
        default=IMAGE_FILE,
        help="需要分析的图片",
    )

    parser.add_argument(
        "--reset",
        action="store_true",
        help="清除剧情状态",
    )

    parser.add_argument(
        "--list-models",
        action="store_true",
        help="列出当前 API Key 可用模型",
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="检查依赖、配置、图片和 API Key",
    )

    parser.add_argument(
        "--loop",
        action="store_true",
        help="按有限轮数和时间持续运行 AI 导演",
    )

    parser.add_argument(
        "--loop-interval",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="循环两次 API 调用间隔，默认 30 秒，最少 10 秒",
    )

    parser.add_argument(
        "--max-rounds",
        type=int,
        default=360,
        metavar="N",
        help="单次循环最多调用 AI 的次数，默认 360，最多 720",
    )

    parser.add_argument(
        "--max-minutes",
        type=float,
        default=60.0,
        metavar="MINUTES",
        help="单次循环最长时间，默认 60 分钟，最多 120 分钟",
    )

    parser.add_argument(
        "--max-frame-age",
        type=float,
        default=120.0,
        metavar="SECONDS",
        help="图片允许的最大年龄，默认 120 秒",
    )

    args = parser.parse_args()

    if args.loop_interval < 10:
        parser.error("--loop-interval 不能小于 10 秒")
    if not 1 <= args.max_rounds <= 720:
        parser.error("--max-rounds 必须在 1 到 720 之间")
    if not 0 < args.max_minutes <= 120:
        parser.error("--max-minutes 必须大于 0 且不超过 120")
    if not 10 <= args.max_frame_age <= 3600:
        parser.error("--max-frame-age 必须在 10 到 3600 秒之间")

    if args.reset:
        STATE_FILE.unlink(
            missing_ok=True
        )

        print(
            "AI 剧情状态已经清除"
        )

        return 0

    if args.list_models:
        try:
            return list_models()

        except Exception as error:
            print(
                f"[API错误] {error}",
                file=sys.stderr,
            )
            return 1

    if args.check:
        try:
            return check_setup(args.image)

        except Exception as error:
            print(
                f"[自检错误] {error}",
                file=sys.stderr,
            )
            return 1

    if not args.image.exists():
        print(
            f"找不到图片: {args.image}",
            file=sys.stderr,
        )
        return 1

    state = load_state()
    config = load_ai_config()
    rounds = args.max_rounds if args.loop else 1
    deadline = (
        time.monotonic() + args.max_minutes * 60
        if args.loop
        else None
    )

    if args.loop:
        max_output = (
            rounds * config["max_output_tokens"]
        )
        print(
            "[循环限制] "
            f"最多 {rounds} 轮，"
            f"最长 {args.max_minutes:g} 分钟，"
            f"间隔 {args.loop_interval:g} 秒，"
            f"最多约 {max_output} 个输出 token"
        )
        print(
            f"[实时观察] 后台反馈启动后约 "
            f"{load_reaction_observation_seconds():g} 秒重新观察画面"
        )

    completed = 0
    attempts = 0
    feedback_burst_rounds, feedback_rest_rounds = load_feedback_cycle()
    feedback_hits = 0
    rest_remaining = 0
    if args.loop:
        print(
            f"[反馈节奏] 最多连续 {feedback_burst_rounds} 个有效反馈轮次，"
            f"随后强制休息 {feedback_rest_rounds} 轮"
        )

    try:
        while attempts < rounds:
            if (
                deadline is not None
                and time.monotonic() >= deadline
            ):
                print("[循环结束] 已达到时间上限")
                break

            attempts += 1
            print(
                f"\n[轮次] {attempts}/{rounds}"
                if args.loop
                else ""
            )

            reaction_followup = False
            reaction_frame_marker = 0
            try:
                ensure_fresh_frame(
                    args.image,
                    args.max_frame_age,
                )
                feedback_allowed = rest_remaining == 0
                decision = run_round(
                    args.image,
                    state,
                    args.execute,
                    feedback_allowed,
                )
                reaction_followup = (
                    decision.action == "hit"
                    and args.execute
                    and feedback_allowed
                )
                if reaction_followup:
                    try:
                        reaction_frame_marker = args.image.stat().st_mtime_ns
                    except OSError:
                        reaction_frame_marker = 0
                if rest_remaining > 0:
                    rest_remaining -= 1
                    print(f"[强制休息] 剩余 {rest_remaining} 轮")
                elif decision.action == "hit" and args.execute:
                    feedback_hits += 1
                    if feedback_hits >= feedback_burst_rounds:
                        feedback_hits = 0
                        rest_remaining = feedback_rest_rounds
                        print(
                            f"[反馈节奏] 已达到连续反馈上限，"
                            f"接下来休息 {rest_remaining} 轮"
                        )
                completed += 1
            except AuthenticationError as error:
                log_runtime_error(error, attempts)
                if args.execute:
                    stop_feedback_state("AI authentication failed")
                print(
                    f"[循环终止] {error}。请重新启动并输入所选 Provider 的有效 API Key。"
                )
                break
            except Exception as error:
                log_runtime_error(error, attempts)
                if args.execute:
                    stop_feedback_state("AI round failed or connection was lost")
                if not args.loop:
                    raise
                print(
                    f"[本轮失败] {error}；稍后继续下一轮",
                )

            if attempts >= rounds or not args.loop:
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("[循环结束] 已达到时间上限")
                break

            wait_seconds = (
                load_reaction_observation_seconds()
                if reaction_followup
                else args.loop_interval
            )
            if reaction_followup:
                print(
                    f"[实时观察] 后台反馈已运行；{wait_seconds:g} 秒后读取最新画面，"
                    "不等待反馈结束"
                )
                received_new_frame = wait_for_new_frame(
                    args.image,
                    reaction_frame_marker,
                    min(wait_seconds, remaining),
                )
                if not received_new_frame:
                    print(
                        "[实时观察] 快速窗口内没有更新画面；继续等待普通刷新窗口，"
                        "避免重复分析同一张图片",
                        file=sys.stderr,
                    )
                    extra_wait = min(
                        max(0, args.loop_interval - wait_seconds),
                        max(0, deadline - time.monotonic()),
                    )
                    if extra_wait > 0:
                        wait_for_new_frame(
                            args.image,
                            reaction_frame_marker,
                            extra_wait,
                        )
            else:
                time.sleep(min(wait_seconds, remaining))

    except KeyboardInterrupt:
        print("\n[循环结束] 用户中断")
    except Exception as error:
        print(
            f"[运行错误] {error}",
            file=sys.stderr,
        )
        if args.loop and args.execute:
            stop_device_after_loop()
        return 1

    if args.loop and args.execute:
        stop_device_after_loop()

    print(
        f"[完成] AI 请求 {attempts} 轮，"
        f"成功推进 {completed} 轮"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
