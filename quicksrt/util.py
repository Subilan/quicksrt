"""通用工具：日志、子进程、ffprobe、meta 状态管理。"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
from pathlib import Path
from typing import Any

META_FILE = "meta.json"


def setup_logging(workdir: Path | None = None, verbose: bool = False) -> logging.Logger:
    log = logging.getLogger("quicksrt")
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
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
