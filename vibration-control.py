#!/usr/bin/env python3
"""Small CLI client for the GALAKU PowerShell TCP serial bridge."""

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


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 25363
DEFAULT_TIMEOUT = 16.0
MAX_COMMAND_LENGTH = 64
WINDOWS_DETACHED_PROCESS = 0x00000008
WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
ROOT = Path(__file__).resolve().parent
BACKGROUND_STATE_FILE = ROOT / "advanced-background.json"
BACKGROUND_STOP_PREFIX = ".advanced-background-"
WAVEFORM_BACKGROUND_STATE_FILE = ROOT / "waveform-background.json"
WAVEFORM_STOP_PREFIX = ".waveform-background-"
STOP_POLL_SECONDS = 0.1
EFFECT_PATTERNS = ("BUBBLE", "PULSE", "BREATHING", "GRADUALRUB", "RHYTHM")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--advanced":
        return advanced_main(argv[1:])

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        command = build_command(args)
    except ValueError as error:
        parser.error(str(error))

    if args.stop:
        request_active_background_stop()
        request_waveform_background_stop()

    try:
        raw_reply = send_command(args.host, args.port, args.timeout, command)
    except OSError as error:
        print(f"connect/send failed: {error}", file=sys.stderr)
        return 1

    reply = select_protocol_reply(raw_reply, command) or raw_reply.strip() or f"OK SENT {command}"

    print(f"<= {command}")
    print(f"=> {reply}")

    return 3 if reply.startswith("ERR") else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibration-control.py",
        description="Send one GALAKU control command to the PowerShell TCP serial bridge.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bridge host, default: {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bridge TCP port, default: {DEFAULT_PORT}")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help=f"socket timeout seconds, default: {DEFAULT_TIMEOUT}")
    parser.add_argument(
        "--channel",
        choices=("A", "B", "AB"),
        type=str.upper,
        default=None,
        help="target channel; omit to use the configured default",
    )
    parser.add_argument(
        "--duration-ms",
        type=int,
        default=None,
        help="effect duration in milliseconds; locally capped by the bridge",
    )
    parser.add_argument(
        "--pattern",
        choices=EFFECT_PATTERNS,
        type=str.upper,
        default=None,
        help="whitelisted waveform pattern",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ping", action="store_true", help="send PING")
    group.add_argument("--status", action="store_true", help="send STATUS")
    group.add_argument("--scan", action="store_true", help="send SCAN")
    group.add_argument("--services", action="store_true", help="send SERVICES")
    group.add_argument("--stop", action="store_true", help="send STOP")
    group.add_argument("--set", dest="set_level", type=int, metavar="PERCENT", help="compatibility alias: send 0-100 percent")
    group.add_argument("--hit", type=float, metavar="PERCENT", help="compatibility alias: send 0-100 percent")
    group.add_argument("--percent", type=int, metavar="PERCENT", help="send 0-100 percent")
    group.add_argument("--raw", metavar="COMMAND", help="send a raw printable ASCII command")

    return parser


def advanced_main(argv: list[str]) -> int:
    parser = build_advanced_parser()
    args = parser.parse_args(argv)

    if args.duration <= 0:
        parser.error("--time must be positive")
    if args.interval <= 0:
        parser.error("--interval must be positive")

    try:
        action = build_advanced_action(args)
    except ValueError as error:
        parser.error(str(error))

    if args.background:
        return start_advanced_background(argv)

    deadline = time.monotonic() + args.duration
    sent = 0
    run_id = args.background_run

    try:
        while time.monotonic() < deadline:
            if run_id and background_stop_requested(run_id):
                print("background advanced run stopped")
                return 0

            command = action()
            try:
                raw_reply = send_command(args.host, args.port, args.timeout, command)
            except OSError as error:
                print(f"connect/send failed: {error}", file=sys.stderr)
                return 1

            reply = select_protocol_reply(raw_reply, command) or raw_reply.strip() or f"OK SENT {command}"
            sent += 1
            print(f"[{sent}] <= {command}")
            print(f"[{sent}] => {reply}")

            if reply.startswith("ERR"):
                return 3

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if wait_for_interval(min(args.interval, remaining), run_id):
                print("background advanced run stopped")
                return 0
    except KeyboardInterrupt:
        print("interrupted")
        return 130
    finally:
        try:
            send_command(args.host, args.port, args.timeout, "STOP")
        except OSError:
            pass
        if run_id:
            clear_background_run(run_id)

    return 0


def build_advanced_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibration-control.py --advanced",
        description="Run bounded repeated GALAKU feedback commands.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bridge host, default: {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bridge TCP port, default: {DEFAULT_PORT}")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help=f"socket timeout seconds, default: {DEFAULT_TIMEOUT}")
    parser.add_argument("--time", dest="duration", type=float, required=True, help="total run seconds")
    parser.add_argument("--interval", type=float, required=True, help="seconds between each command")
    parser.add_argument(
        "--channel", choices=("A", "B", "AB"), type=str.upper,
        default=None, help="target channel; omit to use the configured default",
    )
    parser.add_argument(
        "--duration-ms", type=int, default=None,
        help="duration of every repeated effect; HIT defaults to 800 ms, SET to 3000 ms",
    )
    parser.add_argument(
        "--pattern", choices=EFFECT_PATTERNS, type=str.upper,
        default="BUBBLE", help="waveform used for every repeated effect",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="start the advanced run in a detached background process",
    )
    parser.add_argument("--background-run", help=argparse.SUPPRESS)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--hit", type=float, metavar="PERCENT", help="compatibility alias: repeat a fixed 0-100 percent effect")
    group.add_argument("--randomhit", type=parse_range, metavar="MIN-MAX", help="compatibility alias: repeat random 0-100 percent effects")
    group.add_argument("--set", dest="set_level", type=int, metavar="PERCENT", help="compatibility alias: repeat a fixed 0-100 percent effect")
    group.add_argument("--randomset", type=parse_range, metavar="MIN-MAX", help="compatibility alias: repeat random 0-100 percent effects")

    return parser


def build_advanced_action(args: argparse.Namespace):
    channel = f" {args.channel}" if args.channel else ""

    def percent_command(percent: float, default_duration_ms: int) -> str:
        if not 0 <= percent <= 100:
            raise ValueError("advanced feedback percent must be inside 0-100")
        duration_ms = args.duration_ms if args.duration_ms is not None else default_duration_ms
        if not 100 <= duration_ms <= 60000:
            raise ValueError("--duration-ms must be between 100 and 60000")
        return normalize_command(
            f"PERCENT{channel} {percent:g} {duration_ms} {args.pattern}"
        )

    if args.hit is not None:
        command = percent_command(args.hit, 800)
        return lambda: command

    if args.randomhit is not None:
        low, high = args.randomhit
        if low < 0 or high > 100:
            raise ValueError("--randomhit must be inside 0-100")
        return lambda: percent_command(random.uniform(low, high), 800)

    if args.set_level is not None:
        command = percent_command(args.set_level, 3000)
        return lambda: command

    if args.randomset is not None:
        low, high = args.randomset
        if low < 0 or high > 100:
            raise ValueError("--randomset must be inside 0-100")
        return lambda: percent_command(random.randint(int(low), int(high)), 3000)

    raise ValueError("no advanced action selected")


def parse_range(value: str) -> tuple[float, float]:
    if "-" not in value:
        raise argparse.ArgumentTypeError("range must look like MIN-MAX")
    left, right = value.split("-", 1)
    try:
        low = float(left)
        high = float(right)
    except ValueError as error:
        raise argparse.ArgumentTypeError("range bounds must be numbers") from error
    if low > high:
        raise argparse.ArgumentTypeError("range must satisfy MIN <= MAX")
    return low, high


def start_advanced_background(argv: list[str]) -> int:
    request_active_background_stop()
    request_waveform_background_stop()
    time.sleep(0.2)
    run_id = uuid.uuid4().hex
    child_args = [arg for arg in argv if arg != "--background"]
    command = [
        sys.executable,
        os.path.abspath(__file__),
        "--advanced",
        *child_args,
        "--background-run",
        run_id,
    ]
    kwargs: dict[str, object] = {
        "cwd": os.path.dirname(os.path.abspath(__file__)),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = WINDOWS_DETACHED_PROCESS | WINDOWS_CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)
    write_background_state(run_id, process.pid)
    if process.poll() is not None:
        clear_background_run(run_id)
    print(f"started background advanced run, pid={process.pid}")
    return 0


def background_stop_file(run_id: str) -> Path:
    return ROOT / f"{BACKGROUND_STOP_PREFIX}{run_id}.stop"


def read_background_state() -> dict[str, object] | None:
    try:
        state = json.loads(BACKGROUND_STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or not isinstance(state.get("run_id"), str):
        return None
    return state


def write_background_state(run_id: str, pid: int) -> None:
    BACKGROUND_STATE_FILE.write_text(
        json.dumps({"run_id": run_id, "pid": pid}),
        encoding="utf-8",
    )


def request_active_background_stop() -> bool:
    state = read_background_state()
    if state is None:
        return False
    background_stop_file(str(state["run_id"])).touch()
    return True


def request_waveform_background_stop() -> bool:
    try:
        state = json.loads(
            WAVEFORM_BACKGROUND_STATE_FILE.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(state, dict) or not isinstance(state.get("run_id"), str):
        return False
    (ROOT / f"{WAVEFORM_STOP_PREFIX}{state['run_id']}.stop").touch()
    return True


def background_stop_requested(run_id: str) -> bool:
    return background_stop_file(run_id).exists()


def clear_background_run(run_id: str) -> None:
    background_stop_file(run_id).unlink(missing_ok=True)
    state = read_background_state()
    if state is not None and state.get("run_id") == run_id:
        BACKGROUND_STATE_FILE.unlink(missing_ok=True)


def wait_for_interval(seconds: float, run_id: str | None) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if run_id and background_stop_requested(run_id):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(STOP_POLL_SECONDS, remaining))


def build_command(args: argparse.Namespace) -> str:
    channel = f" {args.channel}" if args.channel else ""
    if args.duration_ms is not None and not 100 <= args.duration_ms <= 60000:
        raise ValueError("--duration-ms must be between 100 and 60000")
    if args.pattern and args.duration_ms is None:
        raise ValueError("--pattern requires --duration-ms")
    effect = ""
    if args.duration_ms is not None:
        effect = f" {args.duration_ms} {args.pattern or 'BUBBLE'}"
    if args.ping:
        return "PING"
    if args.status:
        return "STATUS"
    if args.scan:
        return "SCAN"
    if args.services:
        return "SERVICES"
    if args.stop:
        return "STOP"
    if args.set_level is not None:
        if not 0 <= args.set_level <= 100:
            raise ValueError("--set must be between 0 and 100 percent")
        return f"SET{channel} {args.set_level}{effect}"
    if args.hit is not None:
        if not 0 <= args.hit <= 100:
            raise ValueError("--hit must be between 0 and 100 percent")
        return normalize_command(f"HIT{channel} {args.hit:g}{effect}")
    if args.percent is not None:
        if not 0 <= args.percent <= 100:
            raise ValueError("--percent must be between 0 and 100")
        return f"PERCENT{channel} {args.percent}{effect}"
    if args.raw is not None:
        return normalize_command(args.raw)
    raise ValueError("no command selected")


def normalize_command(command: str) -> str:
    command = command.replace("\r", " ").replace("\n", " ").strip()
    if not command:
        raise ValueError("command is empty")
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValueError(f"command is longer than {MAX_COMMAND_LENGTH} characters")
    if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in command):
        raise ValueError("command must be printable ASCII")
    return command


def send_command(host: str, port: int, timeout: float, command: str) -> str:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(command.encode("ascii") + b"\n")
        sock.shutdown(socket.SHUT_WR)

        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)

    return b"".join(chunks).decode("utf-8", "replace")


def select_protocol_reply(raw_reply: str, command: str) -> str:
    lines = [
        line.strip()
        for line in raw_reply.splitlines()
        if is_protocol_line(line.strip())
    ]
    if not lines:
        return ""

    expected = expected_prefixes(command)
    matches = [line for line in lines if line.startswith(expected)]
    return (matches or lines)[-1]


def is_protocol_line(line: str) -> bool:
    return (
        line == "PONG"
        or line.startswith("STATUS ")
        or line.startswith("OK ")
        or line.startswith("ERR ")
        or line.startswith("SERVICES ")
    )


def expected_prefixes(command: str) -> tuple[str, ...]:
    verb = command.split(maxsplit=1)[0].upper()
    if verb == "PING":
        return ("PONG",)
    if verb == "STATUS":
        return ("STATUS ",)
    if verb == "SCAN":
        return ("OK SCAN",)
    if verb == "SERVICES":
        return ("OK SERVICES", "SERVICES ", "ERR ")
    if verb == "SET":
        return ("OK SET",)
    if verb == "HIT":
        return ("OK HIT",)
    if verb == "PERCENT":
        return ("OK PERCENT",)
    if verb == "STOP":
        return ("OK STOP",)
    return ("PONG", "STATUS ", "OK ", "ERR ", "SERVICES ")


if __name__ == "__main__":
    raise SystemExit(main())
