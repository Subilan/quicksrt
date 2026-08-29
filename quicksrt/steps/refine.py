"""refine：显示层后处理优化（不动 segments_{lang}.json 原始数据）。

1. 标点优化：去掉主语言文本末尾的句号/逗号等（规则按语言，见 LANG_RULES）
2. 拆句优化：单条字幕超过 max_chars 时，只在分句标点处拆成多条，
   拆出的条目时间按字符数比例从原句时间分配；副语言文本按相同比例同步拆分
3. 前后接缝优化：相邻字幕间隔小于 min_gap 时，前一条延伸至后一条开始，消除闪烁

语言无关：拆句以显示主语言（primary_lang，须为源或目标语言之一）为准，按该语言的
规则表拆句（zh/ja/en 预置，未知语言用通用规则），副语言按字符比例同步拆。
输出 work/<id>/refined.json：
[{id, src_id, start, end, <源语言码>: text, <目标语言码>: text}]，字段 key 为语言码，
text 中可含 \\n 表示行内换行（超长分句）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import util
from ..models import load_segments

STEP = "refine"

_SPACE_CHARS = " \u3000"

# ---------- 拆句语言规则表（语言码 -> 规则；未知语言用通用规则） ----------
# split_punct：分句标点（拆句只能在这些标点处拆）
# strip_punct：句尾去除的标点集合（不含语气标点 ?!；为空表示不去）
# break_after：行内断行的后缀字符（中文虚词/日文助词；为空则仅按空格断）
# space_separator：是否把空格也作为分句分隔符
LANG_RULES = {
    "zh": {
        "split_punct": "，、；",
        "strip_punct": "。．，、；",
        "break_after": "的了和但而是与被将把也还都就才再又很最更",
        "space_separator": False,
    },
    "ja": {
        "split_punct": "。、，",
        "strip_punct": "。、，",
        "break_after": "はがをにでとものこと",
        "space_separator": False,
    },
    "en": {
        "split_punct": ".!?;",
        "strip_punct": "",  # 拉丁系字幕惯例保留句号
        "break_after": "",
        "space_separator": True,
    },
}

# 未知语言兜底：Unicode 句末标点 + 空格断行
_FALLBACK_RULE = {
    "split_punct": ".!?;。！？；，、",
    "strip_punct": "",
    "break_after": "",
    "space_separator": True,
}

_RULE_KEYS = ("split_punct", "strip_punct", "break_after")


def lang_rule(lang: str, overrides: dict | None = None) -> dict:
    """按语言取拆句规则：内置表（未知语言用通用规则）+ [refine] 显式键覆盖。"""
    rule = dict(LANG_RULES.get(lang.strip().lower(), _FALLBACK_RULE))
    if overrides:
        for k in _RULE_KEYS:
            v = overrides.get(k)
            if v not in (None, ""):
                rule[k] = v
    return rule


def strip_end_punct(text: str, punct: str) -> str:
    """去掉末尾的 punct 集合字符（含重复与后续空白），保留问号/感叹号等语气标点。
    punct 为空（如拉丁系规则）则原样返回。拆句保留在分句末尾的逗号也会被去掉。"""
    if not punct:
        return text
    return text.rstrip().rstrip(punct).rstrip()


def _inner_split(text: str, max_chars: int, break_after: str) -> list[str]:
    """超长分句（内部无分句标点）的行内断行：优先空格，其次 break_after 字符（虚词/助词）。"""
    if len(text) <= max_chars:
        return [text]
    lines: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        # 找最后的分断候选：空格（任意语言）或 break_after 字符（如中文虚词）
        cut = -1
        for i in range(len(window) - 1, -1, -1):
            if window[i] in _SPACE_CHARS or (break_after and window[i] in break_after):
                cut = i + 1
                break
        if cut <= 0:
            cut = max_chars
        lines.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip(_SPACE_CHARS)
    lines.append(remaining)
    return lines


def split_text(text: str, max_chars: int, rule: dict, split_on_space: bool | None = None) -> list[str]:
    """在分句标点处拆句，贪心合并到不超过 max_chars。

    rule 为 lang_rule() 的输出；split_on_space 非 None 时覆盖规则表的 space_separator。
    """
    if len(text) <= max_chars:
        return [text]
    punct = rule["split_punct"]
    space_sep = rule["space_separator"] if split_on_space is None else split_on_space
    pieces: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in punct or (space_sep and ch in _SPACE_CHARS):
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
            out.extend(_inner_split(m, max_chars, rule.get("break_after", "")))
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


def split_by_ratio(text: str, parts: list[str]) -> list[str]:
    """副语言按主语言各段字符数比例同步拆分（双语条目一一对应）。"""
    if len(parts) <= 1:
        return [text]
    total = sum(len(p) for p in parts) or 1
    cuts = []
    acc = 0
    for p in parts[:-1]:
        acc += len(p)
        cuts.append(_near_space(text, round(acc / total * len(text))))
    split: list[str] = []
    start = 0
    for c in cuts:
        if c > start:
            split.append(text[start:c].strip())
            start = c
        else:
            split.append("")
    split.append(text[start:].strip())
    # 空段并入前一段（切点重合防御）
    cleaned: list[str] = []
    for p in split:
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
    src_lang = cfg.translate_source_lang()
    tgt_lang = cfg.target_lang()
    if src_lang == tgt_lang:
        raise RuntimeError(f"源语言与目标语言相同: {src_lang}（[translate] source_lang/target_lang 需不同）")
    primary = cfg.primary_lang()
    if primary not in (src_lang, tgt_lang):
        raise RuntimeError(
            f"显示主语言 {primary} 不是源语言 {src_lang} 或目标语言 {tgt_lang}，refine 无法处理"
        )

    src_path = workdir / f"segments_{src_lang}.json"
    tgt_path = workdir / f"segments_{tgt_lang}.json"
    out_path = workdir / "refined.json"
    if not tgt_path.exists():
        raise FileNotFoundError(f"缺少 {tgt_path.name}（先执行 translate）")

    rcfg = cfg.section("refine")
    max_chars = int(rcfg.get("max_chars", 42))
    min_gap = float(rcfg.get("min_gap", 0.35))
    strip = bool(rcfg.get("strip_end_punct", True))
    split_on_space = rcfg.get("split_on_space")  # None = 用规则表默认
    rule = lang_rule(primary, rcfg)
    strip_punct = rule["strip_punct"]
    cfg_key = {
        "max_chars": max_chars, "min_gap": min_gap, "strip_end_punct": strip,
        "split_on_space": split_on_space, "primary_lang": primary,
        "src_lang": src_lang, "tgt_lang": tgt_lang, "rule": rule,
    }

    if not force and util.step_done(meta, STEP, refine=cfg_key) and out_path.exists():
        log.info("[refine] 已完成，跳过")
        return out_path

    src_segs = load_segments(src_path)
    tgt_segs = load_segments(tgt_path)
    if len(src_segs) != len(tgt_segs):
        raise RuntimeError(f"segments 数量不一致: {src_lang}={len(src_segs)} {tgt_lang}={len(tgt_segs)}")

    items: list[dict] = []
    nid = 0
    for s_src, s_tgt in zip(src_segs, tgt_segs):
        p_seg, s_seg = (s_src, s_tgt) if primary == src_lang else (s_tgt, s_src)
        p_text = p_seg.text.strip()
        s_text = s_seg.text.strip()
        parts_p = split_text(p_text, max_chars, rule, split_on_space=split_on_space)
        # 去标点要在拆句之后再做一次：拆出的分句末尾可能暴露新的逗号/句号
        if strip:
            parts_p = [strip_end_punct(p, strip_punct) for p in parts_p]
        parts_s = split_by_ratio(s_text, parts_p)
        if len(parts_p) != len(parts_s):
            parts_s = [s_text] * len(parts_p)  # 防御：切分异常时副语言整段复制
        times = assign_time(p_seg.start, p_seg.end, parts_p)
        for part_p, part_s, (s, e2) in zip(parts_p, parts_s, times):
            items.append(
                {
                    "id": nid,
                    "src_id": p_seg.id,
                    "start": round(s, 3),
                    "end": round(e2, 3),
                    src_lang: part_p if primary == src_lang else part_s,
                    tgt_lang: part_p if primary == tgt_lang else part_s,
                }
            )
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
    log.info("[refine] 完成: %d -> %d 条字幕（primary=%s）", len(tgt_segs), len(items), primary)
    return out_path
