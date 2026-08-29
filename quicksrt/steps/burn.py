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

阴影（[style] shadow）：支持旧式标量（shadow = 1，dx=dy=1 像素、半透明黑）
与新式表 shadow = { dx, dy, blur, color }（偏移/模糊/颜色独立控制）。
blur = 0 时走单层（Style Shadow 字段或 \\xshad/\\yshad 内联，零性能开销）；
blur > 0 时走双 Dialogue 分层（阴影层偏移+模糊色块，正文保持锐利，渲染两遍）。

字幕背景（[style] bg）：libass BorderStyle=3 box，背景按文本实际渲染范围绘制（贴合文本）。
写 bg = { padding, color } 即启用（出现即开，不写或 bg = false 禁用；padding 缺省 0.35、
color 缺省 rgba(0,0,0,0.5)，也支持布尔简写 bg = true/false）；
开启后阴影/描边被忽略（背景替代其可读性作用）。
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


_DEFAULT_SHADOW_COLOR = "rgba(0, 0, 0, 0.5)"
_DEFAULT_BG_COLOR = "rgba(0, 0, 0, 0.5)"  # 背景默认色（与阴影默认色相同）


def _fmt(v: float) -> str:
    """浮点数值输出：整数不带小数点（ASS 数字格式，如 2 而非 2.0）。"""
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


@dataclass(frozen=True)
class Shadow:
    """阴影渲染参数（已解析）：偏移 dx/dy、模糊半径 blur（像素）、颜色 color（ASS &HAABBGGRR）。"""
    dx: float
    dy: float
    blur: float
    color: str


def _parse_shadow(value: Any) -> Shadow:
    """解析 [style] shadow 配置 -> Shadow，支持三种形态。

    - 未配置/留空：零偏移无阴影（dx=dy=blur=0）
    - 数字（旧式）：dx=dy=N、无模糊、默认半透明黑
      （等价 shadow = { dx = N, dy = N }）
    - 表：{ dx, dy, blur, color }，各键可省略：dx/dy 缺省 1、blur 缺省 0、
      color 缺省 rgba(0, 0, 0, 0.5)（CSS 颜色，含透明度）
    非法值（负数、无法解析的颜色、其他类型）抛 RuntimeError。
    """
    if value is None or value == "":
        dx = dy = blur = 0
        color = _DEFAULT_SHADOW_COLOR
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        dx = dy = float(value)
        blur = 0
        color = _DEFAULT_SHADOW_COLOR
    elif isinstance(value, dict):
        dx = float(value.get("dx", 1))
        dy = float(value.get("dy", 1))
        blur = float(value.get("blur", 0))
        color = value.get("color", _DEFAULT_SHADOW_COLOR)
    else:
        raise RuntimeError(
            f"shadow 配置非法: {value!r}（支持数字或表，如 shadow = 2 或 "
            "shadow = { dx = 2, dy = 3, blur = 2, color = \"rgba(0,0,0,0.6)\" }）"
        )
    if dx < 0 or dy < 0 or blur < 0:
        raise RuntimeError(f"shadow 偏移/模糊不能为负: dx={dx} dy={dy} blur={blur}")
    try:
        color_ass = util.parse_ass_color(color)
    except ValueError as e:
        raise RuntimeError(f"shadow.color 配置非法: {e}") from e
    return Shadow(dx, dy, blur, color_ass)


def _style_block(
    width: int, height: int, cfg_style: dict, fontsize: int, margin_v: int, margin_h: int,
    name: str = "Default", font_name: str | None = None, bold: bool = False, italic: bool = False,
    color: str | None = None, *, box: bool = False, box_color: str | None = None,
    box_padding: float = 0.0,
) -> str:
    """生成 Style 行。box=True 时启用 BorderStyle=3（libass box）：

    背景由渲染器按文本实际范围绘制（严格贴合文本，含内边距），
    背景色走 OutlineColour（\2c，含 alpha）——libass 在 Shadow≠0 时
    把 box 本体渲染到 outline 槽、1px 偏移副本渲染到 BackColour 槽，
    因此两者写同一颜色、Shadow 固定 1（偏移副本与本体几乎重叠，同色不可见）；
    此模式下阴影/描边配置被忽略（背景替代其可读性作用）。
    """
    font = font_name or cfg_style.get("zh_font_name", "sans-serif")
    color = util.parse_ass_color(color or cfg_style.get("zh_color", "#FFFFFF"))
    shadow = _parse_shadow(cfg_style.get("shadow"))
    if box:
        box_color = box_color or _parse_bg(cfg_style).color
        outline_color, back_color, border_style = box_color, box_color, 3
        outline, style_shadow = box_padding, 1
    else:
        # BackColour = 阴影色（含 alpha）；Shadow 字段在双层模糊或 x/y 不等偏移时由内联/阴影层控制，写 0
        outline_color = util.parse_ass_color(cfg_style.get("outline_color", "#000000"))
        back_color = shadow.color
        border_style = 1
        outline = cfg_style.get("outline", 2)
        style_shadow = 0 if (shadow.blur > 0 or shadow.dx != shadow.dy) else shadow.dx
    return (
        f"Style: {name},{font},{fontsize},{color},&H000000FF,"
        f"{outline_color},{back_color},"
        f"{1 if bold else 0},{1 if italic else 0},0,0,100,100,0,0,{border_style},"
        f"{_fmt(outline)},{_fmt(style_shadow)},"
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


@dataclass(frozen=True)
class Bg:
    """字幕背景渲染参数（已解析）：开关 enabled、内边距比例 padding（相对字号）、
    颜色 color（ASS &HAABBGGRR，已做 box 双层叠加的 alpha 平方根校正）。"""
    enabled: bool
    padding: float
    color: str


def _parse_bg(cfg_style: dict) -> Bg:
    """解析 [style] bg 配置 -> Bg，支持三种形态。

    - 未配置/留空：禁用（enabled=False）；启用时 padding 缺省 0.35（内边距相对字号
      比例）、color 缺省 rgba(0, 0, 0, 0.5)
    - 布尔简写：bg = true 启用 / bg = false 禁用
    - 表 bg = { padding, color }：出现即启用，各键可省（空表 bg = {} 用默认参数启用）；
      enabled 键不再需要，若写了仍兼容（false 显式禁用）
    （兼容旧式三个独立键 bg_enabled/bg_color/bg_padding_ratio：bg 键优先，否则旧键生效）
    非法值（padding 负/无法转换、颜色无法解析、其他类型）抛 RuntimeError。
    """
    bg = cfg_style.get("bg")
    if bg is None or bg == "":
        # 无 bg 键：回退旧式独立键（bg_enabled 缺省 false = 禁用）
        enabled = bool(cfg_style.get("bg_enabled", False))
        padding_raw = cfg_style.get("bg_padding_ratio", 0.35)
        color = cfg_style.get("bg_color", _DEFAULT_BG_COLOR)
    elif isinstance(bg, bool):
        enabled = bg
        padding_raw, color = 0.35, _DEFAULT_BG_COLOR
    elif isinstance(bg, dict):
        enabled = bool(bg.get("enabled", True))  # 表出现即启用
        padding_raw = bg.get("padding", 0.35)
        color = bg.get("color", _DEFAULT_BG_COLOR)
    else:
        raise RuntimeError(
            f"bg 配置非法: {bg!r}（支持布尔或表，如 bg = true 或 "
            "bg = { padding = 0.35, color = \"rgba(0,0,0,0.5)\" }）"
        )
    try:
        padding = float(padding_raw)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"bg.padding 配置非法: {padding_raw!r}") from e
    if padding < 0:
        raise RuntimeError(f"bg.padding 不能为负: {padding}")
    try:
        color_ass = _bg_ass_color(color)
    except ValueError as e:
        raise RuntimeError(f"bg.color 配置非法: {e}") from e
    return Bg(enabled, padding, color_ass)


def _bg_ass_color(color: str) -> str:
    """解析背景颜色（CSS 颜色）-> ASS &HAABBGGRR，并对 alpha 做平方根校正。

    libass 的 BorderStyle=3 box 实际渲染为 box 本体 + 1px 偏移副本两层叠加
    （\bord 内边距下 box 走 OutlineColour，副本走 BackColour），
    视觉不透明度 = 1 - (a'/255)²。令其等于配置语义 1-a/255，解得
    a' = 255·√(a/255)，使配置的透明度（如 rgba(0,0,0,0.5) 半透明黑）在最终渲染中精确生效。
    """
    color = util.parse_ass_color(color)
    a = int(color[2:4], 16)  # &HAA BB GG RR
    a = round(255 * math.sqrt(a / 255))
    return "&H" + f"{a:02X}" + color[4:10]


def _bg_color_parts(bg_color: str) -> tuple[str, str]:
    """拆分 ASS &HAABBGGRR -> (\1c 颜色, \1a alpha)，供内联颜色覆盖使用。"""
    c = bg_color.strip()
    return "&H" + c[4:10], "&H" + c[2:4]


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

    bg = _parse_bg(cfg_style)
    bg_color = bg.color if bg.enabled else None
    bg_pad_p = bg.padding * fontsize if bg.enabled else 0.0
    bg_pad_s = bg.padding * en_size if bg.enabled else 0.0

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{_style_block(width, height, cfg_style, fontsize, margin_v, margin_h, "Default", p_font, p_bold, p_italic, color=p_color, box=bg.enabled, box_color=bg_color, box_padding=bg_pad_p)}
"""
    if mode == "bilingual":
        header += _style_block(width, height, cfg_style, en_size, margin_v, margin_h, "Secondary", s_font, s_bold, s_italic, color=s_color, box=bg.enabled, box_color=bg_color, box_padding=bg_pad_s) + "\n"
    header += "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

    shadow = _parse_shadow(cfg_style.get("shadow"))
    text_bottom = height - margin_v  # 文本块底（alignment=2 底部定位，\pos 锚点）
    lines = [header]
    for it in items:
        primary = _ass_escape(it[primary_lang]).replace("\n", "\\N")
        if bg.enabled:
            # 背景模式：BorderStyle=3 box 随文本渲染（libass 按实际文本范围绘制），
            # 每行内联 \bord 设置按各自字号的内边距；阴影层/描边被 box 替代
            ov_p = f"\\bord{_fmt(bg_pad_p)}"
            if p_shear is not None:
                ov_p += f"\\fax{p_shear}"
            primary = f"{{{ov_p}}}{primary}"
            text = primary
            if mode == "bilingual":
                ov_s = f"\\bord{_fmt(bg_pad_s)}"
                if s_shear is not None:
                    ov_s += f"\\fax{s_shear}"
                secondary = _ass_escape(it[secondary_lang]).replace("\n", "\\N")
                secondary = f"{{{ov_s}}}{secondary}"
                text += f"\\N{{\\rSecondary}}{secondary}"
            lines.append(
                f"Dialogue: 0,{_ass_ts(it['start'])},{_ass_ts(it['end'])},Default,,0,0,0,,{text}"
            )
            continue
        if p_shear is not None:
            primary = f"{{\\fax{p_shear}}}{primary}"
        if mode == "bilingual":
            secondary = _ass_escape(it[secondary_lang]).replace("\n", "\\N")
            if s_shear is not None:
                secondary = f"{{\\fax{s_shear}}}{secondary}"
        if shadow.blur > 0:
            # 双层渲染：阴影层（模糊色块，偏移 dx/dy）在文本层之下，正文保持锐利
            # 注意内联颜色不能用 8 位 &HAABBGGRR（libass 不解析），拆成 \1c 6 位 + \1a alpha
            sh_color, sh_alpha = _bg_color_parts(shadow.color)
            text_layer = f"{{\\pos({width / 2:.1f},{text_bottom:.1f})}}{primary}"
            shadow_layer = (
                f"{{\\pos({width / 2 + shadow.dx:.1f},{text_bottom + shadow.dy:.1f})"
                f"\\bord0\\1c{sh_color}\\1a{sh_alpha}\\blur{_fmt(shadow.blur)}}}{primary}"
            )
            if mode == "bilingual":
                text_layer += f"\\N{{\\rSecondary}}{secondary}"
                shadow_layer += (
                    f"\\N{{\\rSecondary\\bord0\\1c{sh_color}\\1a{sh_alpha}\\blur{_fmt(shadow.blur)}}}{secondary}"
                )
            shadow_line = f"Dialogue: 0,{_ass_ts(it['start'])},{_ass_ts(it['end'])},Default,,0,0,0,,{shadow_layer}"
            text_line = f"Dialogue: 0,{_ass_ts(it['start'])},{_ass_ts(it['end'])},Default,,0,0,0,,{text_layer}"
        else:
            # 单层：Style Shadow 字段（dx==dy）或内联 \xshad/\yshad（x/y 不等时，双语每段重设）
            if shadow.dx != shadow.dy:
                sh = f"\\xshad{_fmt(shadow.dx)}\\yshad{_fmt(shadow.dy)}"
                text = f"{{{sh}}}{primary}"
                if mode == "bilingual":
                    text += f"\\N{{\\rSecondary{sh}}}{secondary}"
            else:
                text = primary
                if mode == "bilingual":
                    text += f"\\N{{\\rSecondary}}{secondary}"
            shadow_line = text_line = f"Dialogue: 0,{_ass_ts(it['start'])},{_ass_ts(it['end'])},Default,,0,0,0,,{text}"
        lines.append(shadow_line)
        if shadow.blur > 0:
            lines.append(text_line)
    return "\n".join(lines) + "\n"


def build_ass(srt_path: Path, cfg_style: dict, probe: dict) -> str:
    width, height = probe["width"], probe["height"]
    fontsize = max(12, round(height * float(cfg_style.get("font_size_ratio", 0.05))))
    margin_v = round(height * float(cfg_style.get("margin_v_ratio", 0.05)))
    margin_h = round(width * 0.03)
    font = cfg_style.get("zh_font_name", _DEFAULT_FONT)
    font = _ensure_font(_resolve_font(font, False, False)[0], "zh")
    bg = _parse_bg(cfg_style)
    bg_color = bg.color if bg.enabled else None
    bg_pad = bg.padding * fontsize if bg.enabled else 0.0
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{_style_block(width, height, cfg_style, fontsize, margin_v, margin_h, "Default", font, box=bg.enabled, box_color=bg_color, box_padding=bg_pad)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    shadow = _parse_shadow(cfg_style.get("shadow"))
    text_bottom = height - margin_v
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
        ts_head = f"Dialogue: 0,{_ass_ts(to_sec(ts[0]))},{_ass_ts(to_sec(ts[1]))},Default,,0,0,0,,"
        if bg.enabled:
            text = f"{{\\bord{_fmt(bg_pad)}}}{text}"
            lines.append(ts_head + text)
        elif shadow.blur > 0:
            sh_color, sh_alpha = _bg_color_parts(shadow.color)
            text_layer = f"{{\\pos({width / 2:.1f},{text_bottom:.1f})}}{text}"
            shadow_layer = (
                f"{{\\pos({width / 2 + shadow.dx:.1f},{text_bottom + shadow.dy:.1f})"
                f"\\bord0\\1c{sh_color}\\1a{sh_alpha}\\blur{_fmt(shadow.blur)}}}{text}"
            )
            lines.append(ts_head + shadow_layer)
            lines.append(ts_head + text_layer)
        else:
            if shadow.dx != shadow.dy:
                text = f"{{\\xshad{_fmt(shadow.dx)}\\yshad{_fmt(shadow.dy)}}}{text}"
            lines.append(ts_head + text)
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
