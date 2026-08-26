"""通用工具：日志、子进程、ffprobe、meta 状态管理、颜色解析。"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

META_FILE = "meta.json"

# ---------- 颜色解析（CSS 风格 -> ASS &HAABBGGRR） ----------

_RGB_RE = re.compile(r"rgb\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)", re.I)
_RGBA_RE = re.compile(
    r"rgba\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)", re.I
)
_HEX_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_HEX_DIGITS = frozenset("0123456789ABCDEF")


def _clamp255(v: Any) -> int:
    return max(0, min(255, int(round(float(v)))))


def _alpha_byte(v: Any) -> int:
    """CSS 透明度 0.0-1.0（1=不透明）-> ASS alpha 0-255（ASS 反转：00=不透明，FF=全透明）。"""
    return max(0, min(255, int(round((1.0 - float(v)) * 255))))


def _to_ass(r: int, g: int, b: int, a: int) -> str:
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def parse_ass_color(value: Any) -> str:
    """CSS 风格颜色配置 -> ASS &HAABBGGRR，统一入口。

    支持：
    - rgba(r, g, b, a)：r/g/b 为 0-255，a 为 0.0-1.0（越界自动钳制）
    - rgb(r, g, b)：等价 rgba(..., 1.0)
    - #RGB / #RRGGBB / #RRGGBBAA（8 位时末两位为透明度）
    兼容旧 ASS 格式 &HAABBGGRR（原样保留，统一大写）。
    """
    v = str(value).strip()
    if not v:
        raise ValueError("颜色不能为空")
    if v.lower().startswith("&h"):
        hexpart = v[2:].upper()
        if len(hexpart) == 6:  # 兼容无 alpha 的简写：AA 视为 00
            hexpart = "00" + hexpart
        if len(hexpart) != 8 or not all(c in _HEX_DIGITS for c in hexpart):
            raise ValueError(f"非法 ASS 颜色: {value!r}")
        return "&H" + hexpart
    m = _HEX_RE.fullmatch(v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            r, g, b = (int(c * 2, 16) for c in h)
            a = 0  # 未指定透明度 = 完全不透明（ASS alpha 00）
        elif len(h) == 6:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            a = 0
        else:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            a = 255 - int(h[6:8], 16)  # CSS #RRGGBBAA 的 alpha 字节反转成 ASS alpha
        return _to_ass(r, g, b, a)
    m = _RGBA_RE.fullmatch(v)
    if m:
        r, g, b = (_clamp255(x) for x in m.group(1, 2, 3))
        return _to_ass(r, g, b, _alpha_byte(m.group(4)))
    m = _RGB_RE.fullmatch(v)
    if m:
        r, g, b = (_clamp255(x) for x in m.group(1, 2, 3))
        return _to_ass(r, g, b, 0)  # rgb 无透明度 = 不透明（ASS alpha 00）
    raise ValueError(
        f"无法解析颜色: {value!r}（支持 rgb()/rgba()/#HEX，如 #FFFFFF、rgba(0,0,0,0.5)）"
    )


class _ColoredFormatter(logging.Formatter):
    """终端日志着色（按 level 给 levelname 上色）；format 后恢复原 levelname，避免污染文件日志。"""

    COLORS = {
        logging.DEBUG: "\x1b[90m",       # 灰
        logging.INFO: "\x1b[32m",        # 绿
        logging.WARNING: "\x1b[33m",     # 黄
        logging.ERROR: "\x1b[31m",       # 红
        logging.CRITICAL: "\x1b[41m",    # 红底
    }
    RESET = "\x1b[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno)
        if color is None:
            return super().format(record)
        levelname = record.levelname
        record.levelname = f"{color}{levelname}{self.RESET}"
        try:
            return super().format(record)
        finally:
            record.levelname = levelname


def _color_enabled(color: bool | None) -> bool:
    """日志颜色决策：显式参数 > QUICKSRT_NO_COLOR/NO_COLOR 环境变量 > 终端检测。"""
    if color is not None:
        return color
    if os.environ.get("QUICKSRT_NO_COLOR") or os.environ.get("NO_COLOR"):
        return False
    try:
        return sys.stderr.isatty()
    except Exception:
        return False


def setup_logging(workdir: Path | None = None, verbose: bool = False,
                  color: bool | None = None) -> logging.Logger:
    """初始化 quicksrt logger；终端输出可着色，文件日志永不着色。

    color: None 自动（终端才上色，尊重 QUICKSRT_NO_COLOR/NO_COLOR），True/False 强制。
    """
    log = logging.getLogger("quicksrt")
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(_ColoredFormatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
                    if _color_enabled(color) else fmt)
    log.addHandler(sh)
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(workdir / "quicksrt.log", encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log


def run_cmd(cmd: list[str], log: logging.Logger, timeout: int | None = None) -> subprocess.CompletedProcess:
    log.info("$ %s", " ".join(shlex.quote(c) for c in cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"命令超时: {' '.join(cmd)}") from e
    if proc.stdout.strip():
        log.debug(proc.stdout.strip())
    if proc.returncode != 0:
        err = proc.stderr.strip()[-2000:]
        log.error("命令失败(%s): %s", proc.returncode, err)
        raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{err}")
    return proc


# fontconfig 通用家族名：fc-list 不解析它们，但 libass/fontconfig 总能解析，视为始终可用
_GENERIC_FAMILIES = {"sans-serif", "serif", "monospace"}


def font_available(name: str) -> bool:
    """fontconfig 检查字体是否可用（fc-list 有匹配输出即存在）。

    通用家族名（sans-serif/serif/monospace）视为可用；fc-list 不可用
    （如无 fontconfig 的环境）时返回 True，避免误报。
    """
    if not name:
        return False
    if name.strip().lower() in _GENERIC_FAMILIES:
        return True
    try:
        proc = subprocess.run(
            ["fc-list", name], capture_output=True, text=True, timeout=10
        )
        return bool(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return True


def probe_video(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {proc.stderr.strip()[-1000:]}")
    info = json.loads(proc.stdout)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    astream = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)

    def fps_of(s: dict) -> float | None:
        r = s.get("avg_frame_rate", "0/1")
        try:
            num, den = r.split("/")
            return float(num) / float(den) if float(den) else None
        except (ValueError, ZeroDivisionError):
            return None

    return {
        "duration": float(info["format"].get("duration", 0)),
        "width": int(vstream.get("width", 0)),
        "height": int(vstream.get("height", 0)),
        "fps": fps_of(vstream),
        "video_codec": vstream.get("codec_name", ""),
        "audio_codec": astream.get("codec_name", "") if astream else "",
        "pix_fmt": vstream.get("pix_fmt", ""),
    }


def find_latest_workdir(cfg) -> Path | None:
    """work 下最新视频工作目录（含 meta.json 的子目录；杂目录如 fx/ 不算）。"""
    base = cfg.work_dir
    if not base.exists():
        return None
    dirs = [d for d in base.iterdir() if d.is_dir() and (d / META_FILE).exists()]
    if not dirs:
        return None
    return max(dirs, key=lambda d: d.stat().st_mtime)


def load_meta(workdir: Path) -> dict:
    p = workdir / META_FILE
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_meta(workdir: Path, meta: dict) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def step_done(meta: dict, step: str, **match: Any) -> bool:
    """检查某环节是否已完成且参数未变化。match 里的键值必须与 meta 中对应内容一致。"""
    if meta.get("steps", {}).get(step) != "done":
        return False
    for key, value in match.items():
        if meta.get(key) != value:
            return False
    return True


def fmt_ts(seconds: float) -> str:
    """秒 -> SRT 时间戳 HH:MM:SS,mmm。"""
    ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
