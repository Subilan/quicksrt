"""extract：ffmpeg 从视频提取 16kHz 单声道 WAV，供 ASR 使用。"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import util

STEP = "extract"


def run(cfg, workdir: Path, log: logging.Logger) -> Path:
    meta = util.load_meta(workdir)
    video = workdir / "video.mp4"
    audio = workdir / "audio.wav"

    if not video.exists():
        raise FileNotFoundError(f"缺少视频文件: {video}（先执行 download）")

    if util.step_done(meta, STEP) and audio.exists() and audio.stat().st_size > 0:
        log.info("[extract] 已完成，跳过")
        return audio

    log.info("[extract] 提取音频: %s", audio.name)
    util.run_cmd(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video),
            "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
            "-c:a", "pcm_s16le",
            str(audio),
        ],
        log,
    )
    meta["steps"] = {**meta.get("steps", {}), STEP: "done"}
    util.save_meta(workdir, meta)
    log.info("[extract] 完成: %s", audio)
    return audio
