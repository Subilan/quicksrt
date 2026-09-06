"""refine：显示层后处理优化（可选环节，就地改写 subs.srt）。

输入是 srt 环节生成的基础稿 subs.srt（v2 双语规范，见 srt 模块），
逐 cue 做显示层优化后写回同一个文件，不做任何 JSON 中转：

1. 标点优化：去掉主语言文本末尾的句号/逗号等（规则按语言，见 LANG_RULES）
2. 拆句优化：单条字幕超过 max_chars 时，只在分句标点处拆成多条，
   拆出的条目时间按字符数比例从原句时间分配；副语言文本按相同比例同步拆分
3. 前后接缝优化：相邻字幕间隔小于 min_gap 时，前一条延伸至后一条开始，消除闪烁

基础稿的块内折行只插入换行不删字符，refine 先把它拍平（去掉换行还原原文）
再重新排版，因此 refine 结果与基础稿是否折行无关（幂等、不叠加变形）。

覆写守卫：subs.srt 若已被人工编辑（内容与 meta 记录指纹不一致），refine
默认跳过不覆盖，--force 才覆盖（先备份）。人工精调工作流：改完 subs.srt
直接跑 burn，无需再经过 refine。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import util
from . import srt as srt_step
from .srt import Cue

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
    """超长分句（内部无分句标点）的切分：优先空格，其次 break_after 字符（虚词/助词）。"""
    if len(text) <= max_chars:
        return [text]
    lines: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        # 找最后的切分候选：空格（任意语言）或 break_after 字符（如中文虚词）
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
    返回的每段都不超过 max_chars（超长无标点句按 break_after/空格切细）。
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


def _flatten(lines: list[str]) -> str:
    """块内行拍平回原文：基础稿折行只插入换行不删字符，去掉换行即还原句子。"""
    return "".join(lines)


def _refine_cue(cue: Cue, max_chars: int, rule: dict, split_on_space: bool | None, strip: bool, strip_punct: str) -> list[Cue]:
    """单条 cue 的优化：主块拆句（含去标点），副块按字符比例同步拆，时间按比例分配。

    拆出的每段成为一条独立 cue（时间轴细分）；有副块时与主块一一对应，
    无副块（单语内容）则只拆主块。
    """
    p_text = _flatten(cue.main_lines)
    s_text = _flatten(cue.sub_lines) if cue.sub_lines else ""
    parts_p = split_text(p_text, max_chars, rule, split_on_space=split_on_space)
    if strip:
        parts_p = [strip_end_punct(p, strip_punct) for p in parts_p]
    if s_text:
        parts_s = split_by_ratio(s_text, parts_p)
        if len(parts_s) != len(parts_p):
            parts_s = [s_text] * len(parts_p)  # 防御：切分异常时副语言整段复制
    else:
        parts_s = [""] * len(parts_p)
    times = assign_time(cue.start, cue.end, parts_p)
    out: list[Cue] = []
    for part_p, part_s, (s, e) in zip(parts_p, parts_s, times):
        out.append(Cue(
            start=round(s, 3),
            end=round(e, 3),
            main_lines=[part_p],
            sub_lines=[part_s] if part_s else [],
        ))
    return out


def run(cfg, workdir: Path, log: logging.Logger, force: bool = False) -> Path:
    meta = util.load_meta(workdir)
    srt_path = workdir / "subs.srt"
    if not srt_path.exists():
        raise FileNotFoundError(f"缺少 {srt_path.name}（先执行 srt 生成基础稿）")

    primary = cfg.primary_lang()
    secondary = cfg.secondary_lang()
    if primary == secondary:
        raise RuntimeError(f"主语言与副语言相同: {primary}")

    separator = srt_step.file_separator(cfg, meta)
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
        "block_separator": separator, "rule": rule,
    }

    # 覆写守卫：文件被外部（人工）编辑过，默认不覆盖（比断点跳过更早提示）
    rec = meta.get("subs") or {}
    digest = util.file_digest(srt_path)
    if digest != rec.get("digest"):
        if not force:
            log.warning(
                "[refine] subs.srt 已被外部修改（可能是人工编辑），自动 refine 会覆盖它；"
                "如需覆盖请加 --force（覆盖前自动备份）"
            )
            return srt_path
        log.info("[refine] --force：将覆盖外部修改过的基础稿（先备份）")
        util.backup_file(srt_path, "pre-auto")

    if not force and util.step_done(meta, STEP, refine=cfg_key):
        log.info("[refine] 已完成，跳过")
        return srt_path

    text = srt_path.read_text(encoding="utf-8-sig")
    cues, warnings = srt_step.parse_cues(text, separator)
    for w in warnings:
        log.warning("[refine] 字幕文件: %s", w)
    if not cues:
        raise RuntimeError("subs.srt 无可处理的字幕（文件为空或全部无效）")

    out_cues: list[Cue] = []
    for cue in cues:
        out_cues.extend(_refine_cue(
            cue, max_chars, rule, split_on_space=split_on_space, strip=strip, strip_punct=strip_punct
        ))

    # 前后接缝优化：微小间隔直接填平（前一条延伸至后一条开始）；
    # 原始 ASR 残留的重叠（负 gap）也一并填平，避免两条字幕同时显示
    if min_gap > 0:
        joined = 0
        for i in range(len(out_cues) - 1):
            gap = out_cues[i + 1].start - out_cues[i].end
            if gap < 0 or 0 < gap < min_gap:
                out_cues[i].end = out_cues[i + 1].start
                joined += 1
        if joined:
            log.info("[refine] 接缝优化: 填平 %d 处微小间隔/重叠", joined)

    rendered = srt_step.render_cues(out_cues, separator)
    srt_path.write_text(rendered, encoding="utf-8")
    meta["subs"] = {"owner": "refine", "digest": util.file_digest(srt_path)}
    meta["refine"] = cfg_key
    meta["steps"] = {**meta.get("steps", {}), STEP: "done", "burn": None}
    util.save_meta(workdir, meta)
    log.info(
        "[refine] 完成: %d -> %d 条字幕（primary=%s，max_chars=%d）",
        len(cues), len(out_cues), primary, max_chars,
    )
    return srt_path
