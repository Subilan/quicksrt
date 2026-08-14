"""download：yt-dlp 下载 YouTube 视频，写入 work/<video_id>/video.mp4。"""

from __future__ import annotations

import logging
from pathlib import Path

import yt_dlp

from .. import util

STEP = "download"


def run(url: str, cfg, workdir: Path, log: logging.Logger) -> str:
    meta = util.load_meta(workdir)
    if util.step_done(meta, STEP, url=url):
        log.info("[download] 已完成，跳过（video_id=%s）", meta.get("video_id"))
        return meta["video_id"]

    fmt = cfg.section("download").get("format", "bv*+ba/b")
    ydl_opts = {
        "format": fmt,
        "outtmpl": str(workdir / "video.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
    }

    log.info("[download] 开始下载: %s", url)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    video_id = info.get("id") or workdir.name
    meta.update(
        {
            "url": url,
            "video_id": video_id,
            "title": info.get("title") or video_id,
            "duration": float(info.get("duration") or 0),
            "uploader": info.get("uploader") or "",
            "steps": {**meta.get("steps", {}), STEP: "done"},
        }
    )
    util.save_meta(workdir, meta)
    log.info("[download] 完成: %s (%.1fs)", video_id, meta["duration"])
    return video_id
