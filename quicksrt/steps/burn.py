"""burn：将 SRT 渲染为 ASS 并烧录进视频（libass），音频流 copy 不重编码。

编码策略：按源视频编码器选择对应编码器（libx264/libx265/libsvtav1），
CRF 质量模式 + 慢速 preset，尽量降低二次编码损失。
优先使用 refined.json（双语 ASS），否则回退到 subs.srt 单语。

语言模式（[style] 配置）：
- mode: bilingual（双语，主语言在上、副语言在下）| mono（单语，只显示主语言）
- primary_lang: zh | en（主语言，大字号在上）
中文样式用 zh_font_name/zh_bold/zh_italic/zh_italic_shear，英文用 en_font_name/en_bold/en_italic/en_italic_shear；
字体名可填变体全名（如 "IBM Plex Sans SemiBold"/"Italic"）精确指定字重/斜体，
也支持 fontconfig 模式语法 "Family:style=Medium,weight=500"（libass 按字体全名精确匹配）。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .. import util

STEP = "burn"

ENCODER_DEFAULTS = {
    "libx264": {"preset": "slow", "crf": 18},
    "libx265": {"preset": "medium", "crf": 22},
    "libsvtav1": {"preset": "8", "crf": 30},
}


def _pick_encoder(video_codec: str) -> str:
    codec = video_codec.lower()
    if "h264" in codec or codec in ("avc1", "avc"):
        return "libx264"
    if codec in ("hevc", "h265"):
        return "libx265"
    if "av1" in codec:
        return "libsvtav1"
    return "libx264"


def _resolve_encoder(burn_cfg: dict, cli_encoder: str | None, video_codec: str) -> str:
    """编码器选择优先级：CLI --encoder > config [burn] encoder > 按源编码器自动。"""
    enc = cli_encoder or (burn_cfg.get("encoder") or "").strip() or _pick_encoder(video_codec)
    if enc not in ENCODER_DEFAULTS:
        raise RuntimeError(f"不支持的编码器: {enc}（可选: {', '.join(ENCODER_DEFAULTS)}）")
    return enc


def _ass_ts(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360_000)  # 1 小时 = 360000 厘秒
    m, rem = divmod(rem, 6_000)  # 1 分钟 = 6000 厘秒
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _style_block(
    width: int, height: int, cfg_style: dict, fontsize: int, margin_v: int, margin_h: int,
    name: str = "Default", font_name: str | None = None, bold: bool = False, italic: bool = False,
    color: str | None = None,
) -> str:
    font = font_name or cfg_style.get("zh_font_name", "sans-serif")
    color = util.parse_ass_color(color or cfg_style.get("zh_color", "#FFFFFF"))
    return (
        f"Style: {name},{font},{fontsize},{color},&H000000FF,"
        f"{util.parse_ass_color(cfg_style.get('outline_color', '#000000'))},&H80000000,"
        f"{1 if bold else 0},{1 if italic else 0},0,0,100,100,0,0,1,"
        f"{cfg_style.get('outline', 2)},{cfg_style.get('shadow', 1)},"
        f"2,{margin_h},{margin_h},{margin_v},1"
    )


def _style_mode(cfg_style: dict) -> tuple[str, str]:
    """解析语言模式 (mode, primary_lang)，兼容旧版 bilingual 布尔。"""
    mode = str(cfg_style.get("mode", "")).lower()
    if mode not in ("bilingual", "mono"):
        mode = "bilingual" if bool(cfg_style.get("bilingual", True)) else "mono"
    primary = str(cfg_style.get("primary_lang", "zh")).lower()
    if primary not in ("zh", "en"):
        primary = "zh"
    return mode, primary


# 背景块定位常量（em 相对字号，Noto Sans CJK 实测：行距=1.0em，字形底距块底 0.167em）
_BG_LINE_PITCH = 1.0     # 行距 = 字号
_BG_GLYPH_EM = 0.8       # 字形高（含下行空隙）≈ 0.8em
_BG_DESCENT = 0.167      # 末行字形底到文本块底的空隙


def _bg_color_parts(bg_color: str) -> tuple[str, str]:
    """拆分 &HAABBGGRR -> (\1c 颜色, \1a alpha)。"""
    c = bg_color.strip()
    return "&H" + c[4:10], "&H" + c[2:4]

def _bg_rect(w: float, h: float) -> str:
    """直角矩形 drawing 路径（原点在块左上，\an2\pos 定位左下角）。"""
    return f"m 0 0 l {w:.2f} 0 l {w:.2f} {h:.2f} l 0 {h:.2f} l 0 0"


def _bg_dialogue(it: dict, width: int, height: int, fontsize: int, en_size: int,
                 margin_v: int, margin_h: int, cfg_style: dict, mode: str, primary_lang: str) -> str:
    """背景块 Dialogue：全宽半透明矩形，紧贴文本块（分层渲染，先于文本绘制）。

    libass 实测行为：行内 drawing 配合 \\an2\\pos(W/2, y) 时，块精确渲染为
    x 0..W（1:1 像素，与字号无关）、底边对齐 \\pos 的 y。
    """
    pad = float(cfg_style.get("bg_padding_ratio", 0.35)) * fontsize
    # 每行字号：主语言行用 fontsize，副语言行用 en_size（双语时副语言在后）
    p_lines = it[primary_lang].count("\n") + 1
    pitches = [fontsize] * p_lines
    if mode == "bilingual":
        s_lines = it["en" if primary_lang == "zh" else "zh"].count("\n") + 1
        pitches += [en_size] * s_lines
    last = pitches[-1]
    # 块高 = 前 n-1 行行距和 + 末行字形高 + 上下内边距；块底 = 文本块底减末行 descent 空隙再加下内边距
    block_h = sum(pitches[:-1]) + _BG_GLYPH_EM * last + 2 * pad
    block_bottom = height - margin_v - _BG_DESCENT * last + pad
    bg_color = util.parse_ass_color(cfg_style.get("bg_color", "rgba(0, 0, 0, 0.5)"))
    color, alpha = _bg_color_parts(bg_color)
    return (
        f"Dialogue: 0,{_ass_ts(it['start'])},{_ass_ts(it['end'])},Default,,0,0,0,,"
        f"{{\\an2\\pos({width / 2:.1f},{block_bottom:.2f})}}"
        f"{{\\1c{color}&\\1a{alpha}&\\3a&HFF&\\4a&HFF&}}"
        f"{{\\p1}}{_bg_rect(width, block_h)}{{\\p0}}"
    )


def _lang_color(cfg_style: dict, lang: str) -> str:
    """某语言的主色（ASS &HAABBGGRR）：en 用 en_color（未设置/留空时回退 zh_color），zh 用 zh_color。"""
    if lang == "en":
        return util.parse_ass_color(cfg_style.get("en_color") or cfg_style.get("zh_color", "#FFFFFF"))
    return util.parse_ass_color(cfg_style.get("zh_color", "#FFFFFF"))


def _lang_shear(cfg_style: dict, lang: str) -> str | None:
    """某语言的假斜体倾角（libass \\fax 剪切值）；未设置/留空返回 None。"""
    key = "en_italic_shear" if lang == "en" else "zh_italic_shear"
    v = cfg_style.get(key)
    return v if v not in (None, "") else None


def _resolve_font(cfg_font: str, bold: bool, italic: bool) -> tuple[str, bool, bool]:
    """把字体配置解析为 ASS 实际使用的 (字体名, 粗体, 斜体)。

    - 无冒号：原样使用（变体全名 "IBM Plex Sans SemiBold"、PostScript 名
      "STHeitiSC-Medium" 等均可，libass 按全名/PostScript 名匹配）
    - 含冒号（fontconfig 模式 "Family:style=/weight=/slant="）：写 ASS 用
      "Family Style"（字体全名形式，libass 精确匹配）；该写法查不到
      （字体无对应全名）时回退 family 名，避免整串匹配失败导致全部丢失
    - style/weight/slant 与 style 名中的粗/斜体词映射为粗体/斜体标志，
      与显式 bold/italic（假粗体/假斜体）合并
    """
    family, style, weight, slant = util.parse_font_pattern(cfg_font)
    w_bold, w_italic = util.pattern_flags(style, weight, slant)
    bold = bold or w_bold
    italic = italic or w_italic
    if style is not None:
        full = f"{family} {style}".strip()
        if util.font_available(full):
            return full, bold, italic
        return family, bold, italic  # 无对应全名，退回家族名由渲染器按标志选择
    return family, bold, italic


def _lang_style(cfg_style: dict, lang: str) -> tuple[str, bool, bool]:
    """某语言的字体系列、粗体、斜体标志（已解析为 ASS 实际字体名）。

    字体名可填变体全名（如 "IBM Plex Sans SemiBold"/"Italic"）或
    fontconfig 模式（如 "Heiti SC:style=Medium"）精确指定字重/斜体；
    zh_bold/zh_italic 为假粗体/假斜体（不依赖字体变体）；
    zh_italic_shear 设置时用 \\fax 剪切自定义倾角（此时 Italic 标志关，避免双重倾斜）。
    """
    if lang == "en":
        font = cfg_style.get("en_font_name") or cfg_style.get("zh_font_name", "sans-serif")
        bold = bool(cfg_style.get("en_bold", False))
        italic = bool(cfg_style.get("en_italic", False))
        if _lang_shear(cfg_style, "en") is not None:
            italic = False
        return _resolve_font(font, bold, italic)
    font = cfg_style.get("zh_font_name", "sans-serif")
    bold = bool(cfg_style.get("zh_bold", False))
    italic = bool(cfg_style.get("zh_italic", False))
    if _lang_shear(cfg_style, "zh") is not None:
        italic = False
    return _resolve_font(font, bold, italic)


_DEFAULT_FONT = "sans-serif"  # fontconfig 通用家族名，任何系统均可解析并回退到系统默认中文字体


def _ensure_font(font: str, ctx: str) -> str:
    """字体在系统中不存在时给出警告并回退默认字体（默认字体也不存在则仅警告，保持原名）。"""
    if util.font_available(font):
        return font
    log = logging.getLogger("quicksrt")
    log.warning(
        "[burn] 字体 %r（%s）在系统中找不到，将回退到默认字体 %r",
        font, ctx, _DEFAULT_FONT,
    )
    if util.font_available(_DEFAULT_FONT):
        return _DEFAULT_FONT
    log.warning("[burn] 默认字体 %r 也不存在，保持原字体名，由渲染器自行兜底", _DEFAULT_FONT)
    return font


def build_ass_items(items: list[dict], cfg_style: dict, probe: dict,
                    mode: str = "bilingual", primary_lang: str = "zh") -> str:
    """从 refined 条目生成 ASS。

    主语言在上（Default，大字号），双语时副语言在下（Secondary，更小字号）；
    primary_lang 决定主语言是 zh 还是 en，mono 模式只渲染主语言。
    """
    width, height = probe["width"], probe["height"]
    fontsize = max(12, round(height * float(cfg_style.get("font_size_ratio", 0.05))))
    margin_v = round(height * float(cfg_style.get("margin_v_ratio", 0.05)))
    margin_h = round(width * 0.03)
    en_size = max(10, round(fontsize * float(cfg_style.get("en_font_ratio", 0.6))))

    secondary_lang = "en" if primary_lang == "zh" else "zh"
    p_font, p_bold, p_italic = _lang_style(cfg_style, primary_lang)
    s_font, s_bold, s_italic = _lang_style(cfg_style, secondary_lang)
    p_font = _ensure_font(p_font, f"primary({primary_lang})")
    s_font = _ensure_font(s_font, f"secondary({secondary_lang})")
    p_color = _lang_color(cfg_style, primary_lang)
    s_color = _lang_color(cfg_style, secondary_lang)
    p_shear = _lang_shear(cfg_style, primary_lang)
    s_shear = _lang_shear(cfg_style, secondary_lang)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{_style_block(width, height, cfg_style, fontsize, margin_v, margin_h, "Default", p_font, p_bold, p_italic, color=p_color)}
"""
    if mode == "bilingual":
        header += _style_block(width, height, cfg_style, en_size, margin_v, margin_h, "Secondary", s_font, s_bold, s_italic, color=s_color) + "\n"
    header += "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

    lines = [header]
    for it in items:
        primary = _ass_escape(it[primary_lang]).replace("\n", "\\N")
        if p_shear is not None:
            primary = f"{{\\fax{p_shear}}}{primary}"
        if mode == "bilingual":
            secondary = _ass_escape(it[secondary_lang]).replace("\n", "\\N")
            if s_shear is not None:
                secondary = f"{{\\fax{s_shear}}}{secondary}"
            text = f"{primary}\\N{{\\rSecondary}}{secondary}"
        else:
            text = primary
        if bool(cfg_style.get("bg_enabled", False)):
            lines.append(
                _bg_dialogue(it, width, height, fontsize, en_size, margin_v, margin_h, cfg_style, mode, primary_lang)
            )
        lines.append(
            f"Dialogue: 0,{_ass_ts(it['start'])},{_ass_ts(it['end'])},Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"


def build_ass(srt_path: Path, cfg_style: dict, probe: dict) -> str:
    width, height = probe["width"], probe["height"]
    fontsize = max(12, round(height * float(cfg_style.get("font_size_ratio", 0.05))))
    margin_v = round(height * float(cfg_style.get("margin_v_ratio", 0.05)))
    margin_h = round(width * 0.03)
    font = cfg_style.get("zh_font_name", _DEFAULT_FONT)
    font = _ensure_font(_resolve_font(font, False, False)[0], "zh")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fontsize},{util.parse_ass_color(cfg_style.get('zh_color', '#FFFFFF'))},&H000000FF,{util.parse_ass_color(cfg_style.get('outline_color', '#000000'))},&H80000000,0,0,0,0,100,100,0,0,1,{cfg_style.get('outline', 2)},{cfg_style.get('shadow', 1)},2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for raw in srt_path.read_text(encoding="utf-8").split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split("\n", 2)
        if len(parts) < 3:
            continue
        ts = parts[1].split(" --> ")
        if len(ts) != 2:
            continue

        def to_sec(t: str) -> float:
            hh, mm, ss = t.replace(",", ".").split(":")
            return int(hh) * 3600 + int(mm) * 60 + float(ss)

        # 注意顺序：先转义 \ { }，再把 \n 换成 ASS 换行符 \N（\N 的反斜杠不能再被转义）
        text = _ass_escape(parts[2].replace("\r", ""))
        text = text.replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{_ass_ts(to_sec(ts[0]))},{_ass_ts(to_sec(ts[1]))},Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"


def run(cfg, workdir: Path, log: logging.Logger, force: bool = False, encoder: str | None = None) -> Path:
    meta = util.load_meta(workdir)
    video = workdir / "video.mp4"
    srt_path = workdir / "subs.srt"
    if not video.exists():
        raise FileNotFoundError(f"缺少视频文件: {video}（先执行 download）")
    if not srt_path.exists():
        raise FileNotFoundError(f"缺少字幕文件: {srt_path}（先执行 srt）")

    out_dir = cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    title = re.sub(r'[\\/:*?"<>|\s]+', "_", meta.get("title", workdir.name)).strip("_")[:80]
    output = out_dir / f"{title}.mp4"

    style_cfg = cfg.style_config()
    mode, primary_lang = _style_mode(style_cfg)
    style_key = dict(style_cfg)

    if not force and util.step_done(meta, STEP, style=style_key) and output.exists():
        log.info("[burn] 已完成，跳过")
        return output

    probe = util.probe_video(video)
    log.info(
        "[burn] 源视频: %dx%d %.2ffps, %s, pix_fmt=%s",
        probe["width"], probe["height"], probe.get("fps") or 0,
        probe["video_codec"], probe["pix_fmt"],
    )
    if probe.get("pix_fmt", "").lower() not in ("", "yuv420p"):
        log.warning("[burn] 源像素格式 %s 非 yuv420p，输出将转为 yuv420p（兼容性优先）", probe["pix_fmt"])

    ass_path = workdir / "subs.ass"
    refined_path = workdir / "refined.json"
    if refined_path.exists():
        items = json.loads(refined_path.read_text(encoding="utf-8"))
        ass = build_ass_items(items, style_cfg, probe, mode=mode, primary_lang=primary_lang)
        log.info("[burn] 使用 refined 字幕（%d 条，mode=%s primary=%s）", len(items), mode, primary_lang)
    else:
        ass = build_ass(srt_path, style_cfg, probe)
    ass_path.write_text(ass, encoding="utf-8")

    enc = _resolve_encoder(cfg.section("burn"), encoder, probe["video_codec"])
    defaults = ENCODER_DEFAULTS[enc]
    burn_cfg = cfg.section("burn")
    preset = burn_cfg.get("preset") or defaults["preset"]
    crf = burn_cfg.get("crf") or defaults["crf"]

    # filter 内路径用单引号包裹，防止空格/特殊字符问题
    filter_str = f"ass=filename='{str(ass_path).replace(chr(39), chr(92) + chr(39))}'"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-vf", filter_str,
        "-c:v", enc, "-crf", str(crf), "-preset", str(preset),
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(output),
    ]
    log.info("[burn] 编码器 %s crf=%s preset=%s -> %s", enc, crf, preset, output)
    util.run_cmd(cmd, log, timeout=None)

    meta["steps"] = {**meta.get("steps", {}), STEP: "done"}
    meta["burn"] = {"encoder": enc, "crf": crf, "preset": preset, "style": style_key}
    util.save_meta(workdir, meta)
    log.info("[burn] 完成: %s", output)
    return output
