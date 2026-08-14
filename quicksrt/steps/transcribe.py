"""transcribe：阿里云百炼 Qwen3-ASR-Flash-Filetrans 异步转写。

流程：提交任务 -> 轮询状态 -> 下载结果 JSON -> 解析为统一 segments 格式。
断点：task_id 落盘可恢复轮询；结果落盘后跳过。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import requests

from .. import util
from ..models import Segment, Word, save_segments

STEP = "transcribe"

SUBMIT_PATH = "/services/audio/asr/transcription"
TASK_PATH = "/tasks/{task_id}"


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }


def submit_task(endpoint: str, api_key: str, audio_url: str, asr_cfg: dict) -> str:
    payload = {
        "model": asr_cfg["model"],
        "input": {"file_url": audio_url},
        "parameters": {
            "channel_id": [0],
            "enable_itn": bool(asr_cfg.get("enable_itn", False)),
            "enable_words": bool(asr_cfg.get("enable_words", True)),
        },
    }
    language = asr_cfg.get("language", "").strip()
    if language:
        payload["parameters"]["language"] = language

    resp = requests.post(endpoint + SUBMIT_PATH, headers=_headers(api_key), json=payload, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"ASR 任务提交失败 (HTTP {resp.status_code}): {resp.text[:500]}")
    data = resp.json()
    task_id = (data.get("output") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"ASR 提交响应异常: {json.dumps(data, ensure_ascii=False)[:500]}")
    return task_id


def poll_task(endpoint: str, api_key: str, task_id: str, interval: float, timeout: float, log: logging.Logger) -> dict:
    deadline = time.time() + timeout
    while True:
        resp = requests.get(endpoint + TASK_PATH.format(task_id=task_id), headers=_headers(api_key), timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"ASR 任务查询失败 (HTTP {resp.status_code}): {resp.text[:500]}")
        data = resp.json()
        status = ((data.get("output") or {}).get("task_status") or "").upper()
        if status in ("SUCCEEDED", "FAILED", "UNKNOWN"):
            if status != "SUCCEEDED":
                raise RuntimeError(f"ASR 任务失败: {json.dumps(data, ensure_ascii=False)[:800]}")
            return data
        if time.time() > deadline:
            raise TimeoutError(f"ASR 任务超时（{timeout:.0f}s），task_id={task_id}，可重跑本环节恢复轮询")
        time.sleep(interval)
        log.info("[transcribe] 任务 %s 状态: %s，%.0fs 后重查", task_id, status, interval)


def download_result(data: dict, workdir: Path, log: logging.Logger) -> Path:
    """Qwen3-ASR-Flash-Filetrans: output.result.transcription_url（单对象）。"""
    output = data.get("output") or {}
    result = output.get("result") or {}
    url = result.get("transcription_url") or result.get("transcriptionUrl")
    if not url:
        # 兼容 Qwen-Audio-3.0/Fun-ASR 的数组形态
        results = output.get("results") or []
        for item in results:
            if item.get("subtask_status") == "SUCCEEDED" and item.get("transcription_url"):
                url = item["transcription_url"]
                break
    if not url:
        raise RuntimeError(f"ASR 结果中没有 transcription_url: {json.dumps(data, ensure_ascii=False)[:800]}")

    raw_path = workdir / "asr_raw.json"
    log.info("[transcribe] 下载识别结果 -> %s", raw_path.name)
    resp = requests.get(url, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f"ASR 结果下载失败 (HTTP {resp.status_code})")
    raw_path.write_bytes(resp.content)
    return raw_path


def parse_result(raw_path: Path) -> list[Segment]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    sentences = []
    for transcript in raw.get("transcripts", []):
        sentences.extend(transcript.get("sentences", []))
    if not sentences:
        raise RuntimeError("ASR 结果中无 sentences，请检查 asr_raw.json")

    segments = []
    for idx, s in enumerate(sentences):
        start = int(s["begin_time"]) / 1000.0
        end = int(s["end_time"]) / 1000.0
        if end <= start:
            end = start + 0.1
        words = []
        for w in s.get("words", []) or []:
            words.append(Word(w.get("text", ""), int(w["begin_time"]) / 1000.0, int(w["end_time"]) / 1000.0))
        segments.append(Segment(id=idx, start=start, end=end, text=s.get("text", ""), words=words))
    return segments


def run(cfg, workdir: Path, log: logging.Logger, force: bool = False) -> list[Segment]:
    meta = util.load_meta(workdir)
    asr_cfg = cfg.section("asr")
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY 环境变量")
    audio_url = meta.get("audio_url")
    if not audio_url:
        raise FileNotFoundError("meta 中没有 audio_url（先执行 upload）")

    seg_path = workdir / "segments_en.json"
    raw_path = workdir / "asr_raw.json"
    task_path = workdir / "asr_task.json"

    if not force and util.step_done(meta, STEP, **{"asr.model": asr_cfg["model"]}) and seg_path.exists():
        log.info("[transcribe] 已完成，跳过")
        return util.load_segments(seg_path)

    endpoint = cfg.asr_endpoint

    task_id = None
    if task_path.exists():
        task_info = json.loads(task_path.read_text(encoding="utf-8"))
        if task_info.get("model") == asr_cfg["model"]:
            task_id = task_info["task_id"]
            log.info("[transcribe] 恢复轮询 task_id=%s", task_id)

    if not task_id:
        log.info("[transcribe] 提交转写任务 (model=%s)", asr_cfg["model"])
        task_id = submit_task(endpoint, api_key, audio_url, asr_cfg)
        task_path.write_text(json.dumps({"task_id": task_id, "model": asr_cfg["model"]}, ensure_ascii=False), encoding="utf-8")
        log.info("[transcribe] 任务已提交: %s", task_id)

    result = poll_task(
        endpoint, api_key, task_id,
        interval=float(asr_cfg.get("poll_interval", 5)),
        timeout=float(asr_cfg.get("poll_timeout", 7200)),
        log=log,
    )
    download_result(result, workdir, log)
    task_path.unlink(missing_ok=True)

    segments = parse_result(raw_path)
    save_segments(seg_path, segments)
    meta.update(
        {
            "asr": {"model": asr_cfg["model"], "language": asr_cfg.get("language", ""), "provider": asr_cfg.get("provider", "aliyun")},
            "steps": {**meta.get("steps", {}), STEP: "done"},
        }
    )
    util.save_meta(workdir, meta)
    log.info("[transcribe] 完成: %d 条句子", len(segments))
    return segments
