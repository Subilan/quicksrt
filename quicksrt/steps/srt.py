"""srt：从中文 segments 生成规范化 SRT 字幕。

规范化规则：时长 clamp（min/max）、相邻字幕不重叠、长句按标点断为最多两行。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import util
from ..models import Segment, load_segments

STEP = "srt"

_PUNCT = "。！？；，、.!?;, "

def _find_cut(text: str, max_chars: int) -> int:
    """在不超过 max_chars 的范围内，找最后一个标点/空格作为断点。"""
    for i in range(min(max_chars, len(text)) - 1, -1, -1):
        if text[i] in _PUNCT:
            return i + 1
    return max_chars


def _split_lines(text: str, max_chars: int, max_lines: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    lines: list[str] = []
    remaining = text
    while len(remaining) > max_chars and len(lines) < max_lines - 1:
        cut = _find_cut(remaining, max_chars)
        lines.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip(" ")
    lines.append(remaining)
    return lines


def normalize(segments: list[Segment], cfg_srt: dict) -> list[Segment]:
    max_dur = float(cfg_srt.get("max_duration", 7.0))
    min_dur = float(cfg_srt.get("min_duration", 0.5))
    max_chars = int(cfg_srt.get("max_line_chars", 42))
    max_lines = int(cfg_srt.get("max_lines", 2))

    out = [Segment(id=s.id, start=s.start, end=s.end, text=s.text.strip(), words=s.words) for s in segments]
    for s in out:
        dur = s.end - s.start
        if dur > max_dur:
            s.end = s.start + max_dur
        elif dur < min_dur:
            s.end = s.start + min_dur
    for i in range(len(out) - 1):
        if out[i].end > out[i + 1].start:
            out[i].end = max(out[i].start + 0.1, out[i + 1].start - 0.05)
    for s in out:
        lines = _split_lines(s.text, max_chars, max_lines)
        s.text = "\n".join(lines)
    return out


def render(segments: list[Segment]) -> str:
    blocks = []
    for idx, s in enumerate(segments, start=1):
        blocks.append(
            f"{idx}\n{util.fmt_ts(s.start)} --> {util.fmt_ts(s.end)}\n{s.text}\n"
        )
    return "\n".join(blocks)


def run(cfg, workdir: Path, log: logging.Logger, force: bool = False) -> Path:
    meta = util.load_meta(workdir)
    zh_path = workdir / "segments_zh.json"
    srt_path = workdir / "subs.srt"
    if not zh_path.exists():
        raise FileNotFoundError(f"缺少 {zh_path.name}（先执行 translate）")

    if not force and util.step_done(meta, STEP) and srt_path.exists():
        log.info("[srt] 已完成，跳过")
        return srt_path

    segments = normalize(load_segments(zh_path), cfg.section("srt"))
    srt_path.write_text(render(segments), encoding="utf-8")
    meta["steps"] = {**meta.get("steps", {}), STEP: "done"}
    util.save_meta(workdir, meta)
    log.info("[srt] 完成: %d 条字幕 -> %s", len(segments), srt_path)
    return srt_path
