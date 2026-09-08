#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from director.waveforms import (
    load_library,
    save_library,
    validate_sequence,
    validate_waveform,
)
from director.pulse_import import (
    import_inbox,
    import_waveform_source,
    load_catalog,
)


ROOT = Path(__file__).resolve().parent
LIBRARY_FILE = ROOT / "waveform-library.json"
PULSE_ROOT = ROOT / "waveforms"
PULSE_CATALOG_FILE = PULSE_ROOT / "catalog.json"
STATE_FILE = ROOT / "waveform-background.json"
STOP_PREFIX = ".waveform-background-"
ADVANCED_STATE_FILE = ROOT / "advanced-background.json"
WINDOWS_DETACHED_PROCESS = 0x00000008
WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200

# Windows 的传统 GBK 控制台无法表示少数波形名字符。保留可显示文字，
# 对单个不兼容字符使用替代符，避免列表/导入命令因此崩溃。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")


def send_command(host: str, port: int, timeout: float, command: str) -> str:
    with socket.create_connection((host, port), timeout=timeout) as client:
        client.settimeout(timeout)
        client.sendall((command + "\n").encode("ascii"))
        chunks = []
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    return b"".join(chunks).decode("utf-8", errors="replace").strip()


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def request_stop(state_file: Path, prefix: str) -> None:
    state = read_json(state_file)
    if state and isinstance(state.get("run_id"), str):
        (ROOT / f"{prefix}{state['run_id']}.stop").touch()


def stop_requested(run_id: str) -> bool:
    return (ROOT / f"{STOP_PREFIX}{run_id}.stop").exists()


def wait_ms(milliseconds: int, run_id: str | None) -> bool:
    deadline = time.monotonic() + milliseconds / 1000
    while time.monotonic() < deadline:
        if run_id and stop_requested(run_id):
            return True
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    return False


def clear_state(run_id: str) -> None:
    state = read_json(STATE_FILE)
    if state and state.get("run_id") == run_id:
        STATE_FILE.unlink(missing_ok=True)
    (ROOT / f"{STOP_PREFIX}{run_id}.stop").unlink(missing_ok=True)


def load_waveform(args: argparse.Namespace) -> tuple[str, dict]:
    if args.file:
        try:
            raw = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"无法读取波形文件: {error}") from error
        steps = validate_sequence(raw.get("steps") if isinstance(raw, dict) else None)
        return str(raw.get("name", "temporary")), {"steps": steps}
    library = load_library(LIBRARY_FILE)
    waveform = library["waveforms"].get(args.name)
    if waveform is None:
        raise ValueError(f"找不到波形: {args.name}")
    return args.name, waveform


def scaled_percent(base: int, relative: int) -> int:
    return max(0, min(100, round(base * relative / 100)))


def run_waveform(args: argparse.Namespace) -> int:
    name, waveform = load_waveform(args)
    steps = waveform["steps"]
    deadline = time.monotonic() + args.time
    run_id = args.background_run
    sent = 0
    print(f"playing waveform={name} intensity={args.intensity}% time={args.time:g}s")
    try:
        while time.monotonic() < deadline:
            for step in steps:
                if time.monotonic() >= deadline or (run_id and stop_requested(run_id)):
                    return 0
                step_deadline = min(deadline, time.monotonic() + step["duration_ms"] / 1000)
                if step["mode"] == "rest":
                    send_command(args.host, args.port, args.timeout, "STOP")
                    if wait_ms(round((step_deadline - time.monotonic()) * 1000), run_id):
                        return 0
                    continue
                while time.monotonic() < step_deadline:
                    if run_id and stop_requested(run_id):
                        return 0
                    if step["mode"] == "fixed":
                        relative = step["relative_percent"]
                    else:
                        relative = random.randint(
                            step["min_relative_percent"],
                            step["max_relative_percent"],
                        )
                    percent = scaled_percent(args.intensity, relative)
                    max_interval_for_duty = max(
                        100, 60000 * 100 // args.min_duty
                    )
                    interval_ms = min(
                        step["interval_ms"], max_interval_for_duty
                    )
                    minimum_pulse_ms = (
                        interval_ms * args.min_duty + 99
                    ) // 100
                    pulse_duration_ms = max(
                        minimum_pulse_ms,
                        min(60000, interval_ms, step["pulse_duration_ms"]),
                    )
                    command = (
                        f"PERCENT {args.channel} {percent} "
                        f"{pulse_duration_ms} {step['pattern']}"
                    )
                    reply = send_command(args.host, args.port, args.timeout, command)
                    sent += 1
                    print(f"[{sent}] <= {command}")
                    print(f"[{sent}] => {reply}")
                    remaining_ms = round(min(
                        step_deadline - time.monotonic(),
                        interval_ms / 1000,
                    ) * 1000)
                    if remaining_ms > 0 and wait_ms(remaining_ms, run_id):
                        return 0
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        print(f"connect/send failed: {error}", file=sys.stderr)
        return 1
    finally:
        try:
            send_command(args.host, args.port, args.timeout, "STOP")
        except OSError:
            pass
        if run_id:
            clear_state(run_id)
    return 0


def run_imported_pulse(args: argparse.Namespace) -> int:
    catalog = load_catalog(PULSE_CATALOG_FILE)
    waveform = catalog["waveforms"].get(args.pulse_id)
    if not waveform or waveform.get("enabled_for_ai") is not True:
        raise ValueError(f"找不到可用的导入波形: {args.pulse_id}")
    duration_ms = round(args.time * 1000)
    command = (
        f"CUSTOMPERCENT {args.channel} {args.intensity} "
        f"{duration_ms} {args.pulse_id}"
    )
    print(
        f"playing imported pulse={waveform.get('name', args.pulse_id)} "
        f"intensity={args.intensity}% time={args.time:g}s"
    )
    try:
        print(send_command(args.host, args.port, args.timeout, command))
        deadline = time.monotonic() + args.time
        while time.monotonic() < deadline:
            if args.background_run and stop_requested(args.background_run):
                break
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        print(f"connect/send failed: {error}", file=sys.stderr)
        return 1
    finally:
        try:
            send_command(args.host, args.port, args.timeout, "STOP")
        except OSError:
            pass
        if args.background_run:
            clear_state(args.background_run)
    return 0


def start_background(argv: list[str]) -> int:
    request_stop(STATE_FILE, STOP_PREFIX)
    request_stop(ADVANCED_STATE_FILE, ".advanced-background-")
    time.sleep(0.2)
    run_id = uuid.uuid4().hex
    child_args = [argument for argument in argv if argument != "--background"]
    command = [
        sys.executable, str(Path(__file__).resolve()),
        *child_args, "--background-run", run_id,
    ]
    kwargs: dict[str, object] = {
        "cwd": str(ROOT), "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = WINDOWS_DETACHED_PROCESS | WINDOWS_CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    STATE_FILE.write_text(
        json.dumps({"run_id": run_id, "pid": process.pid}), encoding="utf-8"
    )
    print(f"started background waveform, pid={process.pid}")
    return 0


def import_library(path: Path) -> int:
    if path.suffix.lower() in {".pulse", ".zip"}:
        result = import_waveform_source(path, PULSE_ROOT)
        print(
            f"导入完成：可用 {result['accepted']}，弃用 {result['rejected']}，"
            f"总计 {result['total']}"
        )
        return 0
    try:
        incoming = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法导入波形: {error}") from error
    raw_waveforms = incoming.get("waveforms") if isinstance(incoming, dict) else None
    if not isinstance(raw_waveforms, dict):
        raise ValueError("导入文件必须包含 waveforms 对象")
    library = load_library(LIBRARY_FILE)
    imported = {}
    for name, waveform in raw_waveforms.items():
        imported[name] = validate_waveform(name, waveform, source="imported")
        imported[name]["source"] = "imported"
    if len(set(library["waveforms"]) | set(imported)) > 100:
        raise ValueError("导入后波形数量会超过 100 个")
    library["waveforms"].update(imported)
    save_library(LIBRARY_FILE, library)
    print(f"imported {len(raw_waveforms)} waveform(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage and play remembered feedback waveforms")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true")
    group.add_argument("--list-pulses", action="store_true")
    group.add_argument("--scan-inbox", action="store_true")
    group.add_argument("--import", dest="import_file", type=Path)
    group.add_argument("--name")
    group.add_argument("--pulse-id")
    group.add_argument("--file", type=Path)
    parser.add_argument("--time", type=float)
    parser.add_argument("--intensity", type=int, default=100)
    parser.add_argument("--channel", choices=("A", "B", "AB"), default="A")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=25363)
    parser.add_argument("--timeout", type=float, default=16)
    parser.add_argument("--min-duty", type=int, default=70)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--background-run", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.list:
        for name, waveform in load_library(LIBRARY_FILE)["waveforms"].items():
            print(f"{name}: {waveform['description']} ({waveform['source']})")
        return 0
    if args.list_pulses:
        catalog = load_catalog(PULSE_CATALOG_FILE)
        for pulse_id, waveform in catalog["waveforms"].items():
            print(f"{pulse_id}: {waveform.get('name')} | {waveform.get('summary')}")
        print(
            f"可用 {len(catalog['waveforms'])}，"
            f"弃用 {len(catalog.get('rejected', []))}"
        )
        return 0
    if args.scan_inbox:
        try:
            result = import_inbox(PULSE_ROOT)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        print(
            f"扫描完成：可用 {result['accepted']}，弃用 {result['rejected']}，"
            f"总计 {result['total']}"
        )
        return 0
    if args.import_file:
        try:
            return import_library(args.import_file)
        except ValueError as error:
            parser.error(str(error))
    if args.time is None or not 0 < args.time <= 600:
        parser.error("--time 必须大于 0 且不超过 600 秒")
    if not 1 <= args.intensity <= 100:
        parser.error("--intensity 必须为 1-100")
    if not 10 <= args.min_duty <= 100:
        parser.error("--min-duty 必须为 10-100")
    try:
        if args.pulse_id:
            catalog = load_catalog(PULSE_CATALOG_FILE)
            if args.pulse_id not in catalog["waveforms"]:
                raise ValueError(f"找不到导入波形: {args.pulse_id}")
        else:
            load_waveform(args)
    except ValueError as error:
        parser.error(str(error))
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if args.background:
        return start_background(effective_argv)
    return run_imported_pulse(args) if args.pulse_id else run_waveform(args)


if __name__ == "__main__":
    raise SystemExit(main())
