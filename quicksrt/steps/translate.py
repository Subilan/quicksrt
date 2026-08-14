"""translate：DeepSeek 分批翻译英文 segments 为简体中文。

每批独立落盘 work/<id>/batches/tr_NNNN.json，支持细粒度断点续跑。
要求模型返回与输入 id 一一对应的 JSON，数量不一致则重试。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import requests

from .. import util
from ..models import Segment, load_segments, save_segments

STEP = "translate"

SYSTEM_PROMPT = (
    "你是专业的字幕翻译引擎。用户会提供一段 JSON 数组，每个元素是 {\"id\": 数字, \"text\": 英文字幕}。"
    "请将每条 text 翻译成简体中文。硬性要求：\n"
    "1. 输出与输入条目数完全一致，逐条对应，严禁合并、拆分、遗漏或改变顺序；\n"
    "2. 每条译文都要放在与输入相同的 id 下；\n"
    "3. 译文自然口语化，保留标点，长度不要超过原文的 1.5 倍；\n"
    "4. 只输出一个 JSON 对象，格式为 {\"translations\": [{\"id\": 1, \"text\": \"译文\"}, ...]}，不要输出任何其他内容。"
)


def _call_deepseek(api_key: str, base_url: str, model: str, temperature: float, batch: list[dict], log: logging.Logger) -> list[dict]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
        ],
        "temperature": temperature,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=300,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek 调用失败 (HTTP {resp.status_code}): {resp.text[:500]}")
    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    try:
        obj = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"DeepSeek 返回非 JSON: {content[:500]}") from e
    return obj.get("translations", [])


def translate_batch(
    api_key: str, cfg: dict, batch: list[dict], log: logging.Logger, retries: int
) -> list[dict]:
    expected_ids = {b["id"] for b in batch}
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            out = _call_deepseek(
                api_key, cfg["base_url"], cfg["model"], float(cfg.get("temperature", 0.3)), batch, log
            )
            out_ids = {o["id"] for o in out}
            if out_ids != expected_ids:
                raise RuntimeError(
                    f"返回条目与输入不一致（期望 {len(expected_ids)} 条，实际 {len(out_ids)} 条，"
                    f"缺失 id: {sorted(expected_ids - out_ids)}，多余 id: {sorted(out_ids - expected_ids)}）"
                )
            return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[translate] 批次失败（第 %d 次）: %s", attempt, e)
            time.sleep(2 * attempt)
    raise RuntimeError(f"批次翻译重试 %d 次仍失败: {last_err}" % retries)


def _batches(segments: list[Segment], max_chars: int) -> list[list[dict]]:
    batches, cur, cur_len = [], [], 0
    for s in segments:
        item = {"id": s.id, "text": s.text}
        if cur and cur_len + len(s.text) > max_chars:
            batches.append(cur)
            cur, cur_len = [], 0
        cur.append(item)
        cur_len += len(s.text) + 1
    if cur:
        batches.append(cur)
    return batches


def run(cfg, workdir: Path, log: logging.Logger, force: bool = False) -> list[Segment]:
    meta = util.load_meta(workdir)
    tr_cfg = cfg.section("translate")
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")

    en_path = workdir / "segments_en.json"
    zh_path = workdir / "segments_zh.json"
    if not en_path.exists():
        raise FileNotFoundError(f"缺少 {en_path.name}（先执行 transcribe）")

    seg_key = {"model": tr_cfg["model"], "temperature": tr_cfg.get("temperature", 0.3)}
    if not force and util.step_done(meta, STEP, translate=seg_key) and zh_path.exists():
        log.info("[translate] 已完成，跳过")
        return load_segments(zh_path)

    segments = load_segments(en_path)
    batches = _batches(segments, int(tr_cfg.get("batch_max_chars", 3000)))
    batch_dir = workdir / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    log.info("[translate] 共 %d 条句子，分 %d 批", len(segments), len(batches))

    results: dict[int, str] = {}
    retries = int(tr_cfg.get("max_retries", 3))
    for idx, batch in enumerate(batches, start=1):
        bfile = batch_dir / f"tr_{idx:04d}.json"
        if bfile.exists():
            saved = json.loads(bfile.read_text(encoding="utf-8"))
            results.update({o["id"]: o["text"] for o in saved["output"]})
            log.info("[translate] 批次 %d/%d 已缓存，跳过", idx, len(batches))
            continue
        log.info("[translate] 翻译批次 %d/%d（%d 条）", idx, len(batches), len(batch))
        out = translate_batch(api_key, tr_cfg, batch, log, retries)
        bfile.write_text(
            json.dumps({"batch": idx, "input": batch, "output": out}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        results.update({o["id"]: o["text"] for o in out})

    translated: list[Segment] = []
    for s in segments:
        text = results.get(s.id)
        if text is None or not text.strip():
            log.warning("[translate] id=%d 缺译文，保留原文: %s", s.id, s.text[:50])
            text = s.text
        translated.append(Segment(id=s.id, start=s.start, end=s.end, text=text.strip(), words=s.words))

    save_segments(zh_path, translated)
    meta.update(
        {
            "translate": seg_key,
            "steps": {**meta.get("steps", {}), STEP: "done"},
        }
    )
    util.save_meta(workdir, meta)
    log.info("[translate] 完成: %d 条", len(translated))
    return translated
