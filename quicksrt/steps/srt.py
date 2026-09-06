"""srt：生成与解析规范字幕文件 subs.srt（quicksrt 双语规范 v2）。

v2 规范：SRT 骨架（序号可选、HH:MM:SS,mmm 时间行、cue 间空行）。
一条 cue 的文本由「主语言块 + 可选分隔行 + 副语言块」构成：

    主语言第一行
    主语言第二行        <- 块内可多行（仅表示折行）
    --                 <- 分隔行（[srt] block_separator，默认 "--"）
    副语言第一行

- 无分隔行 = 该 cue 只有主语言（单语言内容，或双语中某条删掉了副语言）。
- 主/副角色由分隔行显式界定，不再依赖 "\n 分隔主副语言" 的行位置约定，
  块内多行对角色判定无歧义。
- 机器书写恒为「主一行 + 分隔行 + 副一行」，往返幂等；
  人工增删行/条、改措辞、改时间均可，解析器容错并给出 warning。

本模块的 parse_cues / render_cues 是整个显示链（refine/burn/preview）共用的
权威解析与序列化器。run() 从 segments 生成基础稿：主副两语言都存在时按 id
配对为双语 cue，只有单语言时输出主语言 cue（与旧单语路径同质）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import util
from ..models import load_segments

STEP = "srt"

_PUNCT = "。！？；，、.!?;, "

_TIME_RE = r"(\d{1,2}):(\d{2}):(\d{2})[,.]((?:\d{1,3}))\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.]((?:\d{1,3}))"


@dataclass
class Cue:
    """一条显示字幕：时间段 + 主语言块（可多行）+ 副语言块（可多行，可为空）。

    main_lines/sub_lines 中每行已去尾部空白（可保留行首空格——基础稿折行把
    边界空格留给续行行首，解析保留行首空格即可无损还原）；块内多行表示折行，
    渲染时各行先后显示，角色与行数无关。
    """

    start: float
    end: float
    main_lines: list[str]
    sub_lines: list[str] = field(default_factory=list)


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 10 ** len(ms)


def parse_cues(text: str, separator: str = "--") -> tuple[list[Cue], list[str]]:
    """解析 v2 字幕文本 -> (cues, warnings)。

    容错：BOM/CRLF（由调用方以 utf-8-sig 读取）、序号可有可无、文本行可含
    HTML 标签（<b> 等，解析时剥离）、时间毫秒任意精度、cue 间空行数量任意。
    结构错误（块内找不到时间行）抛 ValueError（带块位置）；结束早于开始自动
    交换、相邻 cue 时间重叠、多条分隔行、无文本 cue 等记录 warning 不中断。
    """
    warnings: list[str] = []
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for bi, block in enumerate(re.split(r"\n[ \t]*\n", raw), start=1):
        lines = [ln.rstrip() for ln in block.split("\n")]  # 保留行首空格（折行边界空格）
        lines = [ln for ln in lines if ln]
        if not lines:
            continue
        stripped = [ln.strip() for ln in lines]  # 结构匹配用（容忍首尾空白）
        tpos = next((i for i, ln in enumerate(stripped) if re.fullmatch(_TIME_RE, ln)), None)
        if tpos is None:
            raise ValueError(
                f"第 {bi} 块无法解析：找不到时间行（前两行: {lines[:2]}）"
            )
        m = re.fullmatch(_TIME_RE, stripped[tpos])
        assert m is not None
        g = m.groups()
        start = _ts_to_sec(*g[:4])
        end = _ts_to_sec(*g[4:])
        tag = stripped[0] if stripped[0].isdigit() else str(bi)
        if end < start:
            warnings.append(f"块 {tag}：结束时间早于开始（{stripped[tpos]}），已交换")
            start, end = end, start
        text = [re.sub(r"</?[a-zA-Z][^>]*>", "", ln) for ln in lines[tpos + 1:]]
        text = [ln for ln in text if ln.rstrip()]
        if not text:
            warnings.append(f"块 {tag}：无文本内容，已跳过")
            continue
        seps = [i for i, ln in enumerate(text) if ln.strip() == separator]
        if not seps:
            main, sub = text, []
        else:
            s = seps[0]
            if len(seps) > 1:
                warnings.append(
                    f"块 {tag}：出现 {len(seps)} 条分隔行 {separator!r}，按第一条切分"
                )
            main, sub = text[:s], text[s + 1:]
        if not main and sub:
            warnings.append(f"块 {tag}：主语言为空而副语言有内容，副语言已并入主语言")
            main, sub = sub, []
        cues.append(Cue(start=start, end=end, main_lines=main, sub_lines=sub))
    for i in range(1, len(cues)):
        if cues[i - 1].end > cues[i].start:
            warnings.append(
                f"第 {i} 条与第 {i + 1} 条字幕时间重叠"
                f"（{cues[i - 1].end:.3f} > {cues[i].start:.3f}），请人工检查"
            )
    return cues, warnings


def render_cues(cues: list[Cue], separator: str = "--") -> str:
    """序列化 cues -> v2 字幕文本（带连续序号）。有副语言块的 cue 写分隔行。"""
    blocks = []
    for idx, c in enumerate(cues, start=1):
        lines = list(c.main_lines)
        if c.sub_lines:
            lines.append(separator)
            lines.extend(c.sub_lines)
        blocks.append(
            f"{idx}\n{util.fmt_ts(c.start)} --> {util.fmt_ts(c.end)}\n"
            + "\n".join(lines)
            + "\n"
        )
    return "\n".join(blocks)


def file_separator(cfg, meta: dict, default: str = "--") -> str:
    """解析现有 subs.srt 应使用的分隔符：优先取生成它的环节记录的 separator。

    srt/refine 各自把 block_separator 存进 meta 快照；文件生成后若用户改了
    配置里的分隔符，仍按记录值解析/书写，避免把旧文件误读成主语言。
    """
    for key in ("refine", "srt"):
        rec = meta.get(key) or {}
        if rec.get("block_separator"):
            return rec["block_separator"]
    return str(cfg.section("srt").get("block_separator", default)) or default


# ---------- 基础稿生成（segments -> subs.srt） ----------


_SPACE_CHARS = " \u3000"
_PUNCT_SPLIT = _PUNCT.translate({ord(" "): None})  # 标点（不含空格）


def _find_cut(text: str, max_chars: int) -> int:
    """在不超过 max_chars 的范围内找断点：标点后断（附着前一行），否则空格前断。

    空格处断时切在空格**之前**（空格留给下一行行首），配合解析时保留行首空格，
    去掉换行即可无损还原原文（refine 依赖）。
    """
    window = text[:max_chars]
    for i in range(len(window) - 1, -1, -1):
        if window[i] in _PUNCT_SPLIT:  # 标点：附着在前一行
            return i + 1
    for i in range(len(window) - 1, -1, -1):
        if window[i] in _SPACE_CHARS:  # 空格：切在空格前，空格留到续行行首
            return i
    return max_chars


def wrap_lines(text: str, max_chars: int, max_lines: int) -> list[str]:
    """无损折行：把长文本折成不超过 max_lines 行，行边界不丢字符。

    折行只插入换行不删除任何字符，故 "\n".join 的结果去掉换行后仍等于原文
    （refine 环节依赖这一点把基础稿的既有折行拍平后重新排版）。
    """
    text = text.strip()
    if not text or len(text) <= max_chars or max_lines <= 1:
        return [text]
    lines: list[str] = []
    remaining = text
    while len(remaining) > max_chars and len(lines) < max_lines - 1:
        cut = _find_cut(remaining, max_chars)
        lines.append(remaining[:cut])
        remaining = remaining[cut:]
    lines.append(remaining)
    return lines


def _clamp_and_detach(times: list[tuple[float, float]], cfg_srt: dict) -> list[tuple[float, float]]:
    """时间规整：时长 clamp（min/max）+ 相邻 cue 不重叠（沿用旧单语 normalize 语义）。"""
    max_dur = float(cfg_srt.get("max_duration", 0))
    min_dur = float(cfg_srt.get("min_duration", 1.0))
    out = [(s, e) for s, e in times]
    for i, (s, e) in enumerate(out):
        dur = e - s
        if max_dur > 0 and dur > max_dur:
            out[i] = (s, s + max_dur)
        elif dur < min_dur:
            out[i] = (s, s + min_dur)
    for i in range(len(out) - 1):
        if out[i][1] > out[i + 1][0]:
            out[i] = (out[i][0], max(out[i][0] + 0.1, out[i + 1][0] - 0.05))
    return out


def _base_cues(
    primary: list[Any],
    secondary: dict[int, Any],
    max_chars: int,
    max_lines: int,
    cfg_srt: dict,
) -> list[Cue]:
    """由主语言 segments（按序）与副语言 id 索引生成基础 cue 列表。

    双语条目按 id 配对（translate 保证一一对应、时间一致）；主语言缺副语言
    配对的条目退化为只有主语言。时间取主语言、按 _clamp_and_detach 规整，
    主/副文本各自无损折行到 max_lines 行。
    """
    times = _clamp_and_detach([(s.start, s.end) for s in primary], cfg_srt)
    cues: list[Cue] = []
    for seg, (s, e) in zip(primary, times):
        main = wrap_lines(seg.text, max_chars, max_lines)
        sub_seg = secondary.get(seg.id)
        sub = wrap_lines(sub_seg.text, max_chars, max_lines) if sub_seg is not None else []
        cues.append(Cue(start=s, end=e, main_lines=main, sub_lines=sub))
    return cues


def run(cfg, workdir: Path, log: logging.Logger, force: bool = False) -> Path:
    """生成基础稿 subs.srt。

    主副两语言 segments 都存在时按 id 配对输出双语 cue；只有单语言时输出
    主语言。覆写守卫：subs.srt 若已被 refine 改写或外部（人工）编辑过，
    默认跳过并提示，--force 才覆盖（先备份）。
    """
    meta = util.load_meta(workdir)
    srt_path = workdir / "subs.srt"

    def _seg_path(lang: str) -> Path:
        return workdir / f"segments_{lang}.json"

    def _has(lang: str) -> bool:
        return lang and _seg_path(lang).exists()

    cfg_srt = cfg.section("srt")
    max_chars = int(cfg_srt.get("max_line_chars", 42))
    max_lines = int(cfg_srt.get("max_lines", 2))
    separator = str(cfg_srt.get("block_separator", "--")) or "--"

    # 先按文件可用性解析实际使用的主/副语言，纳入快照：
    # translate 之后补跑（单语 -> 双语）也会因 resolved 变化自动重建
    want_p, want_s = cfg.primary_lang(), cfg.secondary_lang()
    primary_lang, secondary_lang = want_p, want_s
    if not _has(primary_lang):
        if _has(secondary_lang):
            log.warning(
                "[srt] 缺少 %s segments，改用 %s（单语言；完成翻译后重跑本命令即可升级为双语）",
                _seg_path(primary_lang).name, _seg_path(secondary_lang).name,
            )
            primary_lang, secondary_lang = secondary_lang, primary_lang
        elif not _has(secondary_lang):
            raise FileNotFoundError(
                f"缺少 segments 文件（需 {_seg_path(primary_lang).name} 或 "
                f"{_seg_path(secondary_lang).name}，先执行 transcribe/translate）"
            )
    cfg_key = {
        "primary_lang": primary_lang,
        "secondary_lang": secondary_lang,
        "has_secondary": _has(secondary_lang) and secondary_lang != primary_lang,
        "max_line_chars": max_chars,
        "max_lines": max_lines,
        "min_duration": cfg_srt.get("min_duration", 1.0),
        "max_duration": cfg_srt.get("max_duration", 0),
        "block_separator": separator,
    }

    if srt_path.exists() and not force:
        rec = meta.get("subs") or {}
        current = util.file_digest(srt_path)
        owned = rec.get("digest") == current and rec.get("owner") in ("srt", "refine")
        if rec.get("owner") == "refine" and owned:
            log.info(
                "[srt] subs.srt 由 refine 生成，重新生成基础稿请加 --force（覆盖前自动备份）"
            )
            return srt_path
        if not owned:
            log.warning(
                "[srt] subs.srt 已被外部修改（可能是人工编辑），重新生成会覆盖它；如确需请加 --force"
            )
            return srt_path
    if not force and util.step_done(meta, STEP, srt=cfg_key) and srt_path.exists():
        log.info("[srt] 已完成，跳过")
        return srt_path

    primary_segs = load_segments(_seg_path(primary_lang))

    secondary_segs: dict[int, Any] = {}
    if secondary_lang != primary_lang and _seg_path(secondary_lang).exists():
        secondary_segs = {s.id: s for s in load_segments(_seg_path(secondary_lang))}

    cues = _base_cues(primary_segs, secondary_segs, max_chars, max_lines, cfg_srt)
    if secondary_segs:
        log.info(
            "[srt] 双语基础稿 %s/%s: %d 条字幕（%d 条缺副语言）",
            primary_lang, secondary_lang, len(cues),
            sum(1 for c in cues if not c.sub_lines),
        )
    else:
        log.info("[srt] 单语基础稿 %s: %d 条字幕", primary_lang, len(cues))

    if srt_path.exists():  # --force 覆盖前备份
        util.backup_file(srt_path, "pre-srt")
    srt_path.write_text(render_cues(cues, separator), encoding="utf-8")
    meta["subs"] = {"owner": "srt", "digest": util.file_digest(srt_path)}
    meta["srt"] = cfg_key
    meta["steps"] = {**meta.get("steps", {}), STEP: "done"}
    util.save_meta(workdir, meta)
    return srt_path

