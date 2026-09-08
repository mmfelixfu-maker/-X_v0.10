from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import statistics
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


PULSE_PREFIX = "Dungeonlab+pulse:"
FREQUENCIES = [
    10, 20, 30, 40, 50, 60, 70, 80, 90, 100,
    200, 300, 400, 500, 600, 700, 800, 900, 1000,
]
DURATIONS = [1, 2, 3, 4, 5, 8, 10, 15, 20, 30, 40, 50, 60]
MAX_ARCHIVE_FILES = 2000
MAX_PULSE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_RAW_FRAMES = 6000


@dataclass(frozen=True)
class ParsedPulse:
    frames: list[str]
    strengths: list[int]
    frequencies: list[float]
    sections: int


def _indexed(values: list[int], value: object) -> int:
    try:
        index = int(float(str(value)))
    except (TypeError, ValueError):
        index = 0
    return values[max(0, min(len(values) - 1, index))]


def _encode_frequency(value: float) -> int:
    if 10 <= value <= 100:
        output = value
    elif 100 < value <= 600:
        output = (value - 100) / 5 + 100
    elif 600 < value <= 1000:
        output = (value - 600) / 10 + 200
    elif value < 10:
        output = 10
    else:
        output = 240
    return max(10, min(240, round(output)))


def parse_pulse_text(text: str) -> ParsedPulse:
    text = text.strip().lstrip("\ufeff")
    if not text.lower().startswith(PULSE_PREFIX.lower()):
        raise ValueError("文件不是 Dungeonlab+pulse 格式")
    payload = text[len(PULSE_PREFIX):]
    parts = payload.split("+section+")
    if not parts or not parts[0]:
        raise ValueError("没有找到波形段")
    # 新版导出会在第一段前附带元数据并用 '=' 分隔；部分旧版导出
    # 直接从第一段头部开始。两种格式的 section 数据本身相同。
    if "=" in parts[0]:
        parts[0] = parts[0].split("=", 1)[1]

    frame_pairs: list[tuple[int, int]] = []
    raw_frequencies: list[float] = []
    raw_strengths: list[int] = []
    enabled_sections = 0
    for section_index, section_text in enumerate(parts[:10], start=1):
        if "/" not in section_text:
            raise ValueError(f"第 {section_index} 段缺少 '/' 分隔符")
        header_text, shape_text = section_text.split("/", 1)
        header = header_text.split(",")
        if len(header) < 5:
            raise ValueError(f"第 {section_index} 段头部字段不足")
        if header[4].strip() == "0":
            continue
        points: list[int] = []
        for token in shape_text.split(","):
            if not token.strip():
                continue
            try:
                strength = round(float(token.split("-", 1)[0]))
            except ValueError as error:
                raise ValueError(f"第 {section_index} 段存在无效强度点") from error
            points.append(max(0, min(100, strength)))
        if len(points) < 2:
            raise ValueError(f"第 {section_index} 段至少需要 2 个强度点")

        start_frequency = _indexed(FREQUENCIES, header[0])
        end_frequency = _indexed(FREQUENCIES, header[1])
        duration = _indexed(DURATIONS, header[2])
        try:
            frequency_mode = int(float(header[3]))
        except ValueError:
            frequency_mode = 1
        element_count = max(1, math.ceil(duration / len(points)))
        actual_duration = element_count * len(points)
        for element_index in range(element_count):
            for point_index, strength in enumerate(points):
                current = element_index * len(points) + point_index
                section_progress = current / actual_duration
                element_progress = point_index / len(points)
                if frequency_mode == 2:
                    frequency = start_frequency + (end_frequency - start_frequency) * section_progress
                elif frequency_mode == 3:
                    frequency = start_frequency + (end_frequency - start_frequency) * element_progress
                elif frequency_mode == 4:
                    progress = element_index / (element_count - 1) if element_count > 1 else 0
                    frequency = start_frequency + (end_frequency - start_frequency) * progress
                else:
                    frequency = start_frequency
                raw_frequencies.append(frequency)
                raw_strengths.append(strength)
                frame_pairs.append((_encode_frequency(frequency), strength))
                if len(frame_pairs) > MAX_RAW_FRAMES * 4:
                    raise ValueError("波形过长，超过 10 分钟单周期限制")
        enabled_sections += 1

    if not frame_pairs or enabled_sections == 0:
        raise ValueError("没有可播放的已启用波形段")
    frames: list[str] = []
    for offset in range(0, len(frame_pairs), 4):
        group = frame_pairs[offset:offset + 4]
        group += [group[-1]] * (4 - len(group))
        frames.append("".join(f"{frequency:02X}" for frequency, _ in group) +
                      "".join(f"{strength:02X}" for _, strength in group))
    return ParsedPulse(frames, raw_strengths, raw_frequencies, enabled_sections)


def _display_name(filename: str) -> str:
    return Path(filename).stem.strip()[:120] or "未命名波形"


def _safe_id(name: str, content: bytes) -> str:
    ascii_name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
    digest = hashlib.sha256(content).hexdigest()[:10]
    return f"pulse-{ascii_name or 'custom'}-{digest}"


def analyze_pulse(name: str, parsed: ParsedPulse) -> dict:
    strengths = parsed.strengths
    frequencies = parsed.frequencies
    mean_strength = statistics.fmean(strengths)
    mean_frequency = statistics.fmean(frequencies)
    changes = [abs(right - left) for left, right in zip(strengths, strengths[1:])]
    variation = statistics.fmean(changes) if changes else 0
    zero_ratio = sum(value <= 2 for value in strengths) / len(strengths)

    intensity = "低" if mean_strength < 34 else "中" if mean_strength < 67 else "高"
    speed = "低频" if mean_frequency < 45 else "中频" if mean_frequency < 120 else "高频"
    rhythm = "平稳" if variation < 8 else "起伏" if variation < 28 else "跳变"
    situations = []
    lowered = name.lower()
    keyword_scenes = {
        "呼吸": "铺垫或缓慢环境变化", "测试": "连接测试或开场试探",
        "挑逗": "试探或轻度干扰", "惩罚": "剧情惩罚或强冲突",
        "突然": "突袭或剧情转折", "震动": "持续环境效果",
        "高潮": "剧情高潮", "长时间": "持续环境效果",
        "低频": "缓慢压迫或铺垫", "高频": "急促追逐或高潮",
    }
    for keyword, scene in keyword_scenes.items():
        if keyword in lowered and scene not in situations:
            situations.append(scene)
    if not situations:
        if intensity == "低":
            situations.append("开场试探或轻微环境反馈")
        elif intensity == "高" and (speed == "高频" or rhythm == "跳变"):
            situations.append("剧情高潮或强烈突发事件")
        else:
            situations.append("持续互动或中段推进")
    if zero_ratio >= 0.2:
        situations.append("带停顿的节奏反馈")
    return {
        "intensity_class": intensity,
        "frequency_class": speed,
        "rhythm_class": rhythm,
        "strength_min": min(strengths),
        "strength_max": max(strengths),
        "strength_mean": round(mean_strength, 1),
        "frequency_min_hz": round(min(frequencies), 1),
        "frequency_max_hz": round(max(frequencies), 1),
        "frequency_mean_hz": round(mean_frequency, 1),
        "variation_mean": round(variation, 1),
        "cycle_duration_ms": len(parsed.frames) * 100,
        "situations": situations[:3],
        "summary": f"{intensity}强度、{speed}、{rhythm}；适合{'、'.join(situations[:2])}",
    }


def load_catalog(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"version": 1, "waveforms": {}, "rejected": []}
    if not isinstance(data, dict) or not isinstance(data.get("waveforms"), dict):
        return {"version": 1, "waveforms": {}, "rejected": []}
    data.setdefault("rejected", [])
    return data


def save_catalog(path: Path, catalog: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _decode_zip_name(name: str) -> str:
    try:
        repaired = name.encode("cp437").decode("utf-8")
        if repaired:
            return repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return name


def _unique_path(directory: Path, filename: str) -> Path:
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(filename).name)[:180]
    candidate = directory / (safe_name or "unnamed.pulse")
    counter = 2
    while candidate.exists():
        candidate = directory / f"{Path(safe_name).stem}-{counter}{Path(safe_name).suffix}"
        counter += 1
    return candidate


def import_waveform_source(source: Path, root: Path) -> dict:
    inbox = root / "inbox"
    accepted_dir = root / "default"
    rejected_dir = root / "rejected"
    for directory in (inbox, accepted_dir, rejected_dir):
        directory.mkdir(parents=True, exist_ok=True)
    catalog_path = root / "catalog.json"
    catalog = load_catalog(catalog_path)
    candidates: list[tuple[str, bytes]] = []
    if source.suffix.lower() == ".pulse":
        content = source.read_bytes()
        if len(content) > MAX_PULSE_BYTES:
            raise ValueError("单个 .pulse 文件不能超过 2MB")
        candidates.append((source.name, content))
    elif source.suffix.lower() == ".zip":
        total = 0
        with zipfile.ZipFile(source) as archive:
            infos = [item for item in archive.infolist() if not item.is_dir()]
            if len(infos) > MAX_ARCHIVE_FILES:
                raise ValueError("ZIP 文件数量超过 2000")
            for info in infos:
                name = _decode_zip_name(info.filename)
                pure = PurePosixPath(name.replace("\\", "/"))
                if pure.is_absolute() or ".." in pure.parts:
                    continue
                if pure.suffix.lower() != ".pulse" or "__MACOSX" in pure.parts or pure.name.startswith("._"):
                    continue
                if info.file_size > MAX_PULSE_BYTES:
                    candidates.append((pure.name, b""))
                    continue
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("ZIP 解压后波形总大小超过 100MB")
                candidates.append((pure.name, archive.read(info)))
    else:
        raise ValueError("只支持 .pulse 或 .zip 文件")

    imported = rejected = 0
    for filename, content in candidates:
        display_name = _display_name(filename)
        try:
            if not content:
                raise ValueError("文件为空或超过 2MB")
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = content.decode("gb18030")
            parsed = parse_pulse_text(text)
            pulse_id = _safe_id(display_name, content)
            existing = catalog["waveforms"].get(pulse_id, {})
            existing_source = root.parent / str(existing.get("source_file", ""))
            if existing_source.is_file() and existing_source.resolve().is_relative_to(root.resolve()):
                stored = existing_source
            else:
                stored = _unique_path(accepted_dir, filename)
                stored.write_bytes(content)
            analysis = analyze_pulse(display_name, parsed)
            catalog["waveforms"][pulse_id] = {
                "name": display_name,
                "source_file": stored.relative_to(root.parent).as_posix(),
                "enabled_for_ai": True,
                "sections": parsed.sections,
                "frames": parsed.frames,
                **analysis,
            }
            remaining_rejections = []
            for rejection in catalog.get("rejected", []):
                if rejection.get("name") != display_name:
                    remaining_rejections.append(rejection)
                    continue
                rejected_copy = rejected_dir / Path(str(rejection.get("file", ""))).name
                try:
                    rejected_copy.unlink(missing_ok=True)
                except OSError:
                    pass
            catalog["rejected"] = remaining_rejections
            imported += 1
        except (OSError, ValueError) as error:
            stored = _unique_path(rejected_dir, filename)
            if content:
                stored.write_bytes(content)
            catalog["rejected"].append({"name": display_name, "file": stored.name, "reason": str(error)[:300]})
            rejected += 1
    save_catalog(catalog_path, catalog)
    return {"accepted": imported, "rejected": rejected, "total": len(candidates), "catalog": catalog_path}


def import_inbox(root: Path) -> dict:
    inbox = root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    totals = {"accepted": 0, "rejected": 0, "total": 0}
    processed = root / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    for source in sorted(inbox.iterdir()):
        if not source.is_file() or source.suffix.lower() not in {".pulse", ".zip"}:
            continue
        result = import_waveform_source(source, root)
        for key in totals:
            totals[key] += result[key]
        shutil.move(str(source), str(_unique_path(processed, source.name)))
    return totals


def compact_ai_catalog(path: Path, query: str = "", limit: int = 50) -> dict[str, str]:
    catalog = load_catalog(path)
    query = query.lower()
    ranked = []
    for pulse_id, item in catalog["waveforms"].items():
        if item.get("enabled_for_ai") is not True:
            continue
        searchable = " ".join([str(item.get("name", "")), str(item.get("summary", "")), *item.get("situations", [])]).lower()
        keywords = (
            "开场", "试探", "铺垫", "持续", "互动", "追逐", "冲突", "突袭",
            "转折", "高潮", "休息", "停顿", "低频", "中频", "高频",
            "平稳", "起伏", "跳变", "呼吸", "惩罚", "环境",
        )
        score = sum(3 for keyword in keywords if keyword in query and keyword in searchable)
        name = str(item.get("name", "")).lower()
        score += sum(2 for token in re.findall(r"[a-z0-9]{2,}", name) if token in query)
        ranked.append((score, pulse_id, item))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return {pulse_id: f"{item.get('name')}：{item.get('summary')}" for _, pulse_id, item in ranked[:limit]}
