"""通用工具：日志、子进程、ffprobe、时间戳与颜色解析。"""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

RESET = "\x1b[0m"
LEVEL_COLORS = {
    logging.INFO: "\x1b[32m",
    logging.WARNING: "\x1b[33m",
    logging.ERROR: "\x1b[31m",
}


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = LEVEL_COLORS.get(record.levelno)
        if color is None or not sys.stderr.isatty():
            return super().format(record)
        levelname = record.levelname
        record.levelname = f"{color}{levelname}{RESET}"
        try:
            return super().format(record)
        finally:
            record.levelname = levelname


def setup_logging() -> logging.Logger:
    log = logging.getLogger("qsrt")
    if not log.handlers:
        log.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(_Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        log.addHandler(handler)
    return log


def run_cmd(cmd: list[str], log: logging.Logger | None = None) -> subprocess.CompletedProcess:
    if log:
        log.info("$ %s", " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{proc.stderr.strip()[-1500:]}")
    return proc


def probe_video(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {proc.stderr.strip()[-1000:]}")
    info = json.loads(proc.stdout)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise RuntimeError(f"没有视频流: {path}")
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    num, _, den = video.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0
    return {
        "duration": float(info["format"].get("duration", 0)),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "fps": fps,
        "video_codec": video.get("codec_name", ""),
        "audio_codec": audio.get("codec_name", "") if audio else "",
        "pix_fmt": video.get("pix_fmt", ""),
    }


def fmt_srt_ts(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt_ts(value: str) -> float:
    h, m, rest = value.strip().replace(".", ",").split(":")
    s, _, ms = rest.partition(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms or 0) / 1000


def fmt_ass_ts(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- CSS 风格颜色 -> ASS &HAABBGGRR ----------

_RGB_RE = re.compile(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", re.I)
_RGBA_RE = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)", re.I)
_HEX_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


def parse_ass_color(value: Any) -> str:
    """CSS 颜色（rgb/rgba/#hex）-> ASS &HAABBGGRR，alpha 按 ASS 反转字节。"""
    v = str(value).strip()
    if v.lower().startswith("&h"):
        return "&H" + v[2:].upper().rjust(8, "0")
    m = _HEX_RE.fullmatch(v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            r, g, b, a = (int(c * 2, 16) for c in h) + (0,)
        elif len(h) == 6:
            r, g, b, a = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 0
        else:
            r, g, b, a = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
        return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"
    m = _RGBA_RE.fullmatch(v)
    if m:
        r, g, b = (int(x) for x in m.group(1, 2, 3))
        a = max(0, min(255, round((1 - float(m.group(4))) * 255)))
        return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"
    m = _RGB_RE.fullmatch(v)
    if m:
        r, g, b = (int(x) for x in m.group(1, 2, 3))
        return f"&H00{b:02X}{g:02X}{r:02X}"
    raise ValueError(f"无法解析颜色: {value!r}")


# ---------- fontconfig ----------

_GENERIC_FAMILIES = {"sans-serif", "serif", "monospace"}
_BOLD_WORDS = ("bold", "heavy", "black")
_ITALIC_WORDS = ("italic", "oblique")
_WEIGHT_NAMES = {"regular": 400, "normal": 400, "medium": 500, "semibold": 600, "bold": 700}


def parse_font_pattern(name: str) -> tuple[str, str | None, int | None, bool | None]:
    """解析 fontconfig 字体模式 "Family[:style=..,weight=..]"。

    返回 (family, style, weight, italic)；无冒号时后三者均为 None。
    """
    s = name.strip()
    if ":" not in s:
        return s, None, None, None
    family, _, props = s.partition(":")
    style = None
    weight = None
    italic = None
    for pair in props.split(","):
        key, _, value = pair.partition("=")
        key, value = key.strip().lower(), value.strip()
        if key == "style":
            style = value
        elif key == "weight":
            weight = int(value) if value.isdigit() else _WEIGHT_NAMES.get(value.lower())
        elif key == "slant":
            italic = value.lower() in ("italic", "oblique")
    return family.strip(), style, weight, italic


def pattern_flags(style: str | None, weight: int | None, italic: bool | None) -> tuple[bool, bool]:
    bold = bool(weight and weight >= 600)
    if style:
        low = style.lower()
        bold = bold or any(w in low for w in _BOLD_WORDS)
    italic = bool(italic) or bool(style and any(w in style.lower() for w in _ITALIC_WORDS))
    return bold, italic


def _fc_match(pattern: str) -> bool:
    proc = subprocess.run(["fc-list", pattern], capture_output=True, text=True)
    return bool(proc.stdout.strip())


def font_available(name: str) -> bool:
    if not name:
        return False
    if name.strip().lower() in _GENERIC_FAMILIES:
        return True
    try:
        return _fc_match(name) or _fc_match(f":fullname={name}") or _fc_match(f":postscriptname={name}")
    except OSError:
        return True
