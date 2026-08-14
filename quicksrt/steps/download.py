"""download：yt-dlp 下载 YouTube 视频，写入 work/<video_id>/video.mp4。"""

from __future__ import annotations

import logging
from pathlib import Path

import yt_dlp

from .. import util

STEP = "download"


def _find_done(cfg, url: str) -> tuple[str, Path] | None:
    """在 work 下查找已完成下载的同 URL 记录。"""
    if not cfg.work_dir.exists():
        return None
    for d in cfg.work_dir.iterdir():
        if not d.is_dir():
            continue
        m = util.load_meta(d)
        if m.get("url") == url and m.get("steps", {}).get(STEP) == "done":
            if (d / "video.mp4").exists():
                return m["video_id"], d
    return None


def run(url: str, cfg, workdir: Path, log: logging.Logger, fmt: str | None = None) -> str:
    meta = util.load_meta(workdir)
    done = _find_done(cfg, url)
    if done is not None:
        vid = done[0]
        log.info("[download] 已完成，跳过（video_id=%s）", vid)
        return vid

    fmt = fmt or cfg.section("download").get("format", "bv*+ba/b")
    ydl_opts = {
        "format": fmt,
        "outtmpl": str(cfg.work_dir / "%(id)s" / "video.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "retries": 30,
        "fragment_retries": 30,
        "retry_sleep": 3,
        "continuedl": True,
    }
    cookies = cfg.section("download").get("cookies_from_browser", "").strip()
    if cookies:
        ydl_opts["cookiesfrombrowser"] = (cookies,)
        log.info("[download] 使用浏览器 cookies: %s", cookies)
    remote = cfg.section("download").get("remote_components", "").strip()
    if remote:
        ydl_opts["remote_components"] = remote
        log.info("[download] 远程组件: %s", remote)

    log.info("[download] 开始下载: %s", url)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    video_id = info.get("id") or workdir.name
    item_dir = cfg.work_dir / video_id
    item_dir.mkdir(parents=True, exist_ok=True)
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
    util.save_meta(item_dir, meta)
    log.info("[download] 完成: %s (%.1fs)", video_id, meta["duration"])
    return video_id
