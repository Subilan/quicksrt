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

import difflib
import hashlib
import json
import logging
import re
import shutil
import time
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


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 10 ** len(ms)


_MANUAL_TIME_RE = re.compile(
    r"^(\d{1,2}):(\d{2}):(\d{2})[,.]((?:\d{1,3}))\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.]((?:\d{1,3}))$"
)

def parse_bilingual_srt(text: str) -> tuple[list[tuple[float, float, str, str]], list[str]]:
    """解析人工精修稿双语 SRT -> ((start, end, 主语言行, 副语言行), ...), warnings。

    容忍：BOM/CRLF（由调用方以 utf-8-sig 读取）、序号（可缺失）、文本行包裹的
    <b></b>、块间空行。每条必须恰好两行文本（第一行主语言、第二行副语言）；
    结构错误抛 ValueError（带块位置）。时间毫秒精度任意调整；结束早于开始或
    相邻字幕重叠只记录 warning。
    """
    warnings: list[str] = []
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    cues: list[tuple[float, float, str, str]] = []
    for bi, block in enumerate(re.split(r"\n\s*\n", raw), start=1):
        lines = [ln.strip() for ln in block.strip("\n").split("\n") if ln.strip()]
        if not lines:
            continue
        tpos = next((i for i, ln in enumerate(lines) if _MANUAL_TIME_RE.match(ln)), None)
        if tpos is None:
            raise ValueError(f"第 {bi} 块无法解析：找不到时间行（前两行: {lines[:2]}）")
        g = _MANUAL_TIME_RE.match(lines[tpos]).groups()
        start = _ts_to_sec(*g[:4])
        end = _ts_to_sec(*g[4:])
        tag = lines[0] if lines[0].isdigit() else str(bi)
        if end < start:
            warnings.append(f"块 #{tag}：结束时间早于开始（{lines[tpos]}），已交换")
            start, end = end, start
        txt = [re.sub(r"</?b>", "", ln).strip() for ln in lines[tpos + 1:]]
        txt = [ln for ln in txt if ln]
        if len(txt) != 2:
            raise ValueError(
                f"块 #{tag}：需要恰好两行文本（第一行主语言、第二行副语言），实际 {len(txt)} 行"
            )
        cues.append((start, end, txt[0], txt[1]))
    for i in range(1, len(cues)):
        if cues[i - 1][1] > cues[i][0]:
            warnings.append(
                f"第 {i} 条与第 {i + 1} 条字幕时间重叠（{cues[i - 1][1]:.3f} > {cues[i][0]:.3f}）"
            )
    return cues, warnings


_ALIGN_MIN_RATIO = 0.5


def _norm_text(text: str) -> str:
    return re.sub(r"[^\w]+", "", text).lower()


def assign_src_ids(targets: list[str], ref_texts: list[str], ref_src_ids: list[int]) -> list[int]:
    """尽力溯源：人工稿各条（targets，取副语言文本）对齐到参考 refined 条目的 src_id。

    人工可增删/合并导致条数不一致：逐条取与全部未用参考条文本相似度最高者，
    >= _ALIGN_MIN_RATIO 即继承其 src_id（一条参考只被消费一次）；找不到匹配的标 -1。
    仅溯源用途（显示层不消费 src_id），故为尽力而为。
    """
    used = [False] * len(ref_texts)
    ref_norm = [_norm_text(t) for t in ref_texts]
    out: list[int] = []
    for t in targets:
        target = _norm_text(t)
        best_i, best_r = -1, 0.0
        if target:
            for i, r in enumerate(ref_norm):
                if used[i] or not r:
                    continue
                ratio = difflib.SequenceMatcher(None, target, r).ratio()
                if ratio > best_r:
                    best_r, best_i = ratio, i
        if best_i >= 0 and best_r >= _ALIGN_MIN_RATIO:
            used[best_i] = True
            out.append(ref_src_ids[best_i])
        else:
            out.append(-1)
    return out


def _backup_json(path: Path, tag: str) -> Path | None:
    if not path.exists():
        return None
    bak = path.with_name(f"{path.name}.bak.{tag}-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy(path, bak)
    return bak


def import_manual(workdir: Path, manual_path: Path, meta: dict, log: logging.Logger,
                  cfg_key: dict, primary: str, secondary: str) -> Path:
    """人工精修稿特殊路径：双语 SRT 逐条原样导入 refined.json。

    不做拆句/去标点/接缝，文本与时间 1:1。内容不变时幂等（不重置断点）；
    有变化时先备份旧 refined.json，重置 srt/burn 断点强制下游重跑，并在 meta
    记录 manual_refine 指纹（供后续自动 refine 的防覆盖守卫使用）。
    """
    out_path = workdir / "refined.json"
    cues, warnings = parse_bilingual_srt(manual_path.read_text(encoding="utf-8-sig"))
    for w in warnings:
        log.warning("[refine] 人工精修稿: %s", w)
    ref_items: list[dict] = []
    if out_path.exists():
        try:
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
            ref_items = loaded if isinstance(loaded, list) else []
        except json.JSONDecodeError:
            ref_items = []
    src_ids = assign_src_ids(
        [c[3] for c in cues],
        [it.get(secondary, "") for it in ref_items],
        [it.get("src_id", -1) for it in ref_items],
    )
    items: list[dict] = []
    for i, (s, e, p_text, s_text) in enumerate(cues):
        items.append({
            "id": i, "src_id": src_ids[i], "start": s, "end": e,
            primary: p_text, secondary: s_text,
        })
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    old = out_path.read_text(encoding="utf-8") if out_path.exists() else None
    changed = old is None or old.rstrip("\n") != payload.rstrip("\n")
    if changed:
        _backup_json(out_path, "pre-manual")
        out_path.write_text(payload, encoding="utf-8")
        meta["steps"] = {**meta.get("steps", {}), STEP: "done", "srt": None, "burn": None}
    meta["refine"] = cfg_key
    meta["manual_refine"] = {
        "source": manual_path.name,
        "count": len(items),
        "digest": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12],
        "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    util.save_meta(workdir, meta)
    n_src = sum(1 for x in src_ids if x >= 0)
    log.info(
        "[refine] 人工精修稿已%s: %d 条（%s/%s，来源 %s，src_id 溯源 %d/%d）",
        "更新" if changed else "确认（内容未变）", len(items), primary, secondary,
        manual_path.name, n_src, len(items),
    )
    if changed:
        log.info("[refine] 已重置 srt/burn 断点，请重新运行 quicksrt srt 与 quicksrt burn")
    return out_path


def run(cfg, workdir: Path, log: logging.Logger, force: bool = False,
        manual_srt: Path | None = None) -> Path:
    """自动 refine；manual_srt 非 None 时走人工精修稿特殊路径（见 import_manual）。"""
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
    secondary = cfg.secondary_lang()
    if primary == secondary or secondary not in (src_lang, tgt_lang):
        raise RuntimeError(
            f"主/副语言需分别取源语言 {src_lang} 与目标语言 {tgt_lang}（当前 primary={primary} secondary={secondary}）"
        )

    out_path = workdir / "refined.json"
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

    if manual_srt is not None:
        return import_manual(workdir, manual_srt, meta, log, cfg_key, primary, secondary)

    has_manual = bool(meta.get("manual_refine"))
    if not force and util.step_done(meta, STEP, refine=cfg_key) and out_path.exists():
        if has_manual:
            log.info("[refine] refined.json 来自人工精修稿，跳过自动 refine（如需覆盖请加 --force）")
        else:
            log.info("[refine] 已完成，跳过")
        return out_path
    if has_manual:
        if not force:
            log.warning(
                "[refine] refined.json 来自人工精修稿（%s）；自动 refine 将覆盖它。"
                "如确需自动结果请加 --force，否则请改传 --manual 导入新的精修稿",
                meta["manual_refine"].get("source", "?"),
            )
            return out_path
        log.info("[refine] --force：将用自动结果覆盖人工精修稿（先备份）")

    src_path = workdir / f"segments_{src_lang}.json"
    tgt_path = workdir / f"segments_{tgt_lang}.json"
    if not tgt_path.exists():
        raise FileNotFoundError(f"缺少 {tgt_path.name}（先执行 translate）")

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

    # 覆盖人工稿（--force）前先备份；完成后清除 manual_refine 标记
    if meta.get("manual_refine"):
        _backup_json(out_path, "pre-auto")
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    if meta.pop("manual_refine", None) is not None:
        log.warning("[refine] 已用自动结果覆盖人工精修稿")
    meta.update({"refine": cfg_key, "steps": {**meta.get("steps", {}), STEP: "done"}})
    util.save_meta(workdir, meta)
    log.info("[refine] 完成: %d -> %d 条字幕（primary=%s）", len(tgt_segs), len(items), primary)
    return out_path
