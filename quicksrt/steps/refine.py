"""refine：显示层后处理优化（不动 segments_en/zh 原始数据）。

1. 标点优化：去掉字幕末尾句号（中文字幕惯例不带句号）
2. 拆句优化：单条字幕超过 max_chars 时，只在分句标点（，、；）处拆成多条，
   拆出的条目时间按字符数比例从原句时间分配；英文原文按相同比例同步拆分
3. 前后接缝优化：相邻字幕间隔小于 min_gap 时，前一条延伸至后一条开始，消除闪烁

输出 work/<id>/refined.json：
[{id, src_id, start, end, zh, en}]，zh/en 中可含 \n 表示行内换行（超长分句）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import util
from ..models import load_segments

STEP = "refine"

_SPLIT_PUNCT = "，、；"


def strip_end_punct(text: str) -> str:
    """去掉末尾句号（含重复句号与后续空白），保留问号/感叹号等语气标点。"""
    return text.rstrip().rstrip("。．").rstrip()


def _inner_split(text: str, max_chars: int) -> list[str]:
    """超长分句（内部无逗号）的行内断行：英文在空格处断，中文在虚词后断。"""
    if len(text) <= max_chars:
        return [text]
    lines: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        # 找最后的分断候选：空格（英文）或虚词（中文）
        cut = -1
        for i in range(len(window) - 1, -1, -1):
            if window[i] in " \u3000":
                cut = i + 1
                break
            if window[i] in "的了和但而是与被将把也还都就才再又很最更":
                cut = i + 1
                break
        if cut <= 0:
            cut = max_chars
        lines.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip(" \u3000")
    lines.append(remaining)
    return lines


def split_zh(text: str, max_chars: int) -> list[str]:
    """在分句标点处拆句，贪心合并到不超过 max_chars。"""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _SPLIT_PUNCT:
            pieces.append(buf)
            buf = ""
    if buf:
        pieces.append(buf)

    merged: list[str] = []
    cur = ""
    for p in pieces:
        if not cur:
            cur = p
        elif len(cur) + len(p) <= max_chars:
            cur += p
        else:
            merged.append(cur)
            cur = p
    if cur:
        merged.append(cur)

    out: list[str] = []
    for m in merged:
        if len(m) > max_chars:
            out.extend(_inner_split(m, max_chars))
        else:
            out.append(m)
    return out


def _near_space(text: str, pos: int) -> int:
    """把切点调整到附近最近的空格（±10 字符内），找不到则原样。"""
    for d in range(11):
        for p in (pos + d, pos - d):
            if 0 < p < len(text) and text[p] == " ":
                return p
    return pos


def split_en(text: str, zh_parts: list[str]) -> list[str]:
    """英文按中文字符数比例同步拆分（双语条目一一对应）。"""
    if len(zh_parts) <= 1:
        return [text]
    total = sum(len(p) for p in zh_parts) or 1
    cuts = []
    acc = 0
    for p in zh_parts[:-1]:
        acc += len(p)
        cuts.append(_near_space(text, round(acc / total * len(text))))
    parts: list[str] = []
    start = 0
    for c in cuts:
        if c > start:
            parts.append(text[start:c].strip())
            start = c
        else:
            parts.append("")
    parts.append(text[start:].strip())
    # 空段并入前一段（切点重合防御）
    cleaned: list[str] = []
    for p in parts:
        if not p and cleaned:
            cleaned[-1] += p
        elif p:
            cleaned.append(p)
    return cleaned or [text]


def assign_time(start: float, end: float, parts: list[str]) -> list[tuple[float, float]]:
    """按字符数比例把 [start, end] 分配给各段（首段起点=start，末段终点=end）。"""
    total = sum(len(p) for p in parts) or 1
    times: list[tuple[float, float]] = []
    acc = 0.0
    for p in parts:
        prev_end = times[-1][1] if times else start
        acc += len(p)
        times.append((prev_end, start + (end - start) * (acc / total)))
    times[-1] = (times[-1][0], end)
    return times


def run(cfg, workdir: Path, log: logging.Logger, force: bool = False) -> Path:
    meta = util.load_meta(workdir)
    en_path = workdir / "segments_en.json"
    zh_path = workdir / "segments_zh.json"
    out_path = workdir / "refined.json"
    if not zh_path.exists():
        raise FileNotFoundError(f"缺少 {zh_path.name}（先执行 translate）")

    rcfg = cfg.section("refine")
    max_chars = int(rcfg.get("max_chars", 42))
    min_gap = float(rcfg.get("min_gap", 0.35))
    strip = bool(rcfg.get("strip_end_punct", True))
    cfg_key = {"max_chars": max_chars, "min_gap": min_gap, "strip_end_punct": strip}

    if not force and util.step_done(meta, STEP, refine=cfg_key) and out_path.exists():
        log.info("[refine] 已完成，跳过")
        return out_path

    en_segs = load_segments(en_path)
    zh_segs = load_segments(zh_path)
    if len(en_segs) != len(zh_segs):
        raise RuntimeError(f"segments 数量不一致: en={len(en_segs)} zh={len(zh_segs)}")

    items: list[dict] = []
    nid = 0
    for e, z in zip(en_segs, zh_segs):
        zt = z.text.strip()
        et = e.text.strip()
        if strip:
            zt = strip_end_punct(zt)
        zh_parts = split_zh(zt, max_chars)
        en_parts = split_en(et, zh_parts)
        if len(zh_parts) != len(en_parts):
            en_parts = [et] * len(zh_parts)  # 防御：切分异常时英文整段复制
        times = assign_time(z.start, z.end, zh_parts)
        for part_zh, part_en, (s, e2) in zip(zh_parts, en_parts, times):
            items.append({"id": nid, "src_id": z.id, "start": round(s, 3), "end": round(e2, 3), "zh": part_zh, "en": part_en})
            nid += 1

    # 前后接缝优化：微小间隔直接填平（前一条延伸至后一条开始）；
    # 原始 ASR 残留的重叠（负 gap）也一并填平，避免两条字幕同时显示
    if min_gap > 0:
        joined = 0
        for i in range(len(items) - 1):
            gap = items[i + 1]["start"] - items[i]["end"]
            if gap < 0 or 0 < gap < min_gap:
                items[i]["end"] = items[i + 1]["start"]
                joined += 1
        if joined:
            log.info("[refine] 接缝优化: 填平 %d 处微小间隔/重叠", joined)

    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    meta.update({"refine": cfg_key, "steps": {**meta.get("steps", {}), STEP: "done"}})
    util.save_meta(workdir, meta)
    log.info("[refine] 完成: %d -> %d 条字幕", len(zh_segs), len(items))
    return out_path
