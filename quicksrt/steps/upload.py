"""upload：将音频上传到阿里云 OSS，生成预签名 URL 供 ASR 拉取。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import oss2

from .. import util

STEP = "upload"


def run(cfg, workdir: Path, log: logging.Logger) -> str:
    meta = util.load_meta(workdir)
    audio = workdir / "audio.wav"
    video_id = meta.get("video_id", workdir.name)

    if not audio.exists():
        raise FileNotFoundError(f"缺少音频文件: {audio}（先执行 extract）")

    audio_url = meta.get("audio_url")
    if util.step_done(meta, STEP) and audio_url:
        log.info("[upload] 已完成，跳过")
        return audio_url

    oss_cfg = cfg.section("oss")
    bucket_name = oss_cfg.get("bucket", "").strip()
    if not bucket_name:
        raise RuntimeError("未配置 [oss] bucket，请编辑 config.toml")
    key_id = os.getenv("OSS_ACCESS_KEY_ID", "").strip()
    key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        raise RuntimeError("缺少 OSS 凭证：请设置环境变量 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET")

    auth = oss2.Auth(key_id, key_secret)
    bucket = oss2.Bucket(auth, oss_cfg.get("endpoint", ""), bucket_name)
    key = f"{oss_cfg.get('upload_prefix', 'audio')}/{video_id}.wav"

    log.info("[upload] 上传 %s -> oss://%s/%s", audio.name, bucket_name, key)
    store = oss2.ResumableStore(root=str(workdir / ".oss_upload"))
    oss2.resumable_upload(
        bucket, key, str(audio),
        store=store,
        multipart_threshold=10 * 1024 * 1024,
        part_size=5 * 1024 * 1024,
        num_threads=4,
    )

    days = int(oss_cfg.get("presign_days", 7))
    signed = bucket.sign_url("GET", key, days * 24 * 3600)
    meta.update({"audio_url": signed, "steps": {**meta.get("steps", {}), STEP: "done"}})
    util.save_meta(workdir, meta)
    log.info("[upload] 完成，预签名 URL 有效期 %s 天", days)
    return signed
