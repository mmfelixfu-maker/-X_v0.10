from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from .models import AuthenticationError


IMAGE_DETAIL_LEVELS = {"auto", "low", "high", "original"}


def load_ai_config(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise RuntimeError(f"找不到 AI 配置: {path}") from error
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"无法读取 AI 配置: {error}") from error
    if not isinstance(raw, dict):
        raise RuntimeError("ai-config.json 必须是 JSON 对象")
    if "providers" not in raw:
        active = str(raw.get("provider", "deepseek")).strip().lower()
        raw = {
            "active_provider": active,
            "image_detail": raw.get("image_detail", "auto"),
            "max_output_tokens": raw.get("max_output_tokens", 5000),
            "providers": {active: raw},
        }
    active = str(raw.get("active_provider", "")).strip().lower()
    providers = raw.get("providers")
    if not active or not isinstance(providers, dict) or not isinstance(providers.get(active), dict):
        raise RuntimeError("ai-config.json 的 active_provider 未在 providers 中定义")
    selected = providers[active]
    config = {
        "provider": active,
        "label": str(selected.get("label", active)).strip() or active,
        "model": str(selected.get("model", "")).strip(),
        "base_url": str(selected.get("base_url", "")).strip().rstrip("/"),
        "api_key_env": str(selected.get("api_key_env", "")).strip(),
        "image_detail": str(raw.get("image_detail", "auto")).strip().lower(),
        "max_output_tokens": raw.get("max_output_tokens", 5000),
        "thinking_disabled": bool(selected.get("thinking_disabled", False)),
        "token_field": str(selected.get("token_field", "max_tokens")),
        "available_providers": sorted(providers),
    }
    for key in ("model", "base_url", "api_key_env"):
        if not config[key]:
            raise RuntimeError(f"Provider {active} 缺少 {key}")
    if config["token_field"] not in {"max_tokens", "max_completion_tokens"}:
        raise RuntimeError("token_field 必须是 max_tokens 或 max_completion_tokens")
    if config["image_detail"] not in IMAGE_DETAIL_LEVELS:
        raise RuntimeError("image_detail 必须是 auto、low、high 或 original")
    tokens = config["max_output_tokens"]
    if isinstance(tokens, bool) or not isinstance(tokens, int) or not 100 <= tokens <= 5000:
        raise RuntimeError("max_output_tokens 必须是 100 到 5000 的整数")
    return config


def api_request(config: dict, path: str, api_key: str, payload: dict | None = None) -> dict:
    url = f"{config['base_url']}{path}"
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    body = ""
    forced_resolve = None
    label = config.get("label", config.get("provider", "API"))
    for attempt in range(1, 4):
        command = [
            "curl.exe", "--silent", "--show-error", "--connect-timeout", "20",
            "--max-time", "90", "--request", "POST" if data is not None else "GET",
            "--header", "Accept: application/json", "--header",
            f"Authorization: Bearer {api_key}", "--write-out", "\n%{http_code}",
        ]
        if data is not None:
            command.extend(["--header", "Content-Type: application/json", "--data-binary", "@-"])
        if forced_resolve:
            command.extend(["--resolve", forced_resolve])
        command.append(url)
        try:
            result = subprocess.run(command, input=data, capture_output=True, timeout=100)
        except subprocess.TimeoutExpired as error:
            if attempt < 3:
                print(f"[API重试] 请求超时，第 {attempt}/3 次失败")
                time.sleep(attempt * 2)
                continue
            raise RuntimeError(f"连接 {label} API 超时") from error
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        try:
            body, status_text = stdout.rsplit("\n", 1)
            status_code = int(status_text.strip())
        except (ValueError, TypeError) as error:
            detail = stderr or "curl 未返回 HTTP 状态码"
            if attempt < 3:
                print(f"[API重试] 连接失败，第 {attempt}/3 次: {detail}")
                time.sleep(attempt * 2)
                continue
            raise RuntimeError(f"无法连接 {label} API: {detail}") from error
        if result.returncode != 0:
            detail = stderr or f"curl 退出码 {result.returncode}"
            if forced_resolve is None and ("Could not resolve host" in detail or "Resolving timed out" in detail):
                host = urlsplit(url).hostname
                try:
                    address = socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)[0][4][0]
                    forced_resolve = f"{host}:443:{address}"
                    print(f"[DNS回退] Windows DNS -> {address}")
                    continue
                except (OSError, IndexError, TypeError):
                    pass
            if attempt < 3:
                print(f"[API重试] 连接失败，第 {attempt}/3 次: {detail}")
                time.sleep(attempt * 2)
                continue
            raise RuntimeError(f"无法连接 {label} API: {detail}")
        if status_code in {429, 500, 502, 503, 504} and attempt < 3:
            print(f"[API重试] HTTP {status_code}，第 {attempt}/3 次失败")
            time.sleep(attempt * 2)
            continue
        if status_code in {401, 403}:
            raise AuthenticationError(f"{label} API HTTP {status_code}: Authentication Fails")
        if not 200 <= status_code < 300:
            raise RuntimeError(f"{label} API HTTP {status_code}: {body}")
        break
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{label} API 返回了非 JSON 响应") from error
    if not isinstance(decoded, dict):
        raise RuntimeError(f"{label} API 返回格式异常")
    return decoded
