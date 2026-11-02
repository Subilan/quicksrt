"""burn：把中英两个 SRT 渲染成 ASS 并烧录进视频。"""

from __future__ import annotations

import logging
import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import util
from .config import Config

ENCODERS = {"libx264": ("slow", 18), "libx265": ("medium", 22), "libsvtav1": ("8", 30)}
STYLE_DEFAULTS = {
    "primary_font_name": "sans-serif",
    "secondary_font_name": "sans-serif",
    "font_size_ratio": 0.05,
    "margin_v_ratio": 0.05,
    "margin_h_ratio": 0.03,
    "secondary_font_ratio": 0.6,
    "primary_color": "#FFFFFF",
    "outline_color": "#000000",
    "outline": 2,
    "shadow": 1,
    "mode": "bilingual",
}
_TIME_RE = re.compile(r"(\d+:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d+:\d{2}:\d{2}[,.]\d{1,3})")
_DEFAULT_SHADOW_COLOR = "rgba(0, 0, 0, 0.5)"
_DEFAULT_BG = {"padding": 0.35, "color": "rgba(0, 0, 0, 0.5)"}


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


# ---------- SRT ----------


def read_srt(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[dict] = []
    for block in re.split(r"\n[ \t]*\n", raw):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        pos = next((i for i, line in enumerate(lines) if _TIME_RE.fullmatch(line)), None)
        if pos is None:
            continue
        match = _TIME_RE.fullmatch(lines[pos])
        text = "\n".join(lines[pos + 1 :])
        if not text:
            continue
        cues.append(
            {
                "start": util.parse_srt_ts(match.group(1)),
                "end": util.parse_srt_ts(match.group(2)),
                "text": text,
            }
        )
    return cues


# ---------- 样式解析 ----------


@dataclass(frozen=True)
class Shadow:
    dx: float
    dy: float
    blur: float
    color: str


@dataclass(frozen=True)
class Bg:
    enabled: bool
    padding_x: float
    padding_y: float
    color: str


def _parse_shadow(value: Any) -> Shadow:
    if value is None or value == "":
        dx = dy = blur = 0.0
        color = _DEFAULT_SHADOW_COLOR
    elif isinstance(value, bool):
        dx = dy = float(value)
        blur = 0.0
        color = _DEFAULT_SHADOW_COLOR
    elif isinstance(value, (int, float)):
        dx = dy = float(value)
        blur = 0.0
        color = _DEFAULT_SHADOW_COLOR
    elif isinstance(value, dict):
        dx = float(value.get("dx", 1))
        dy = float(value.get("dy", 1))
        blur = float(value.get("blur", 0))
        color = value.get("color", _DEFAULT_SHADOW_COLOR)
    else:
        raise RuntimeError(f"shadow 配置非法: {value!r}")
    if dx < 0 or dy < 0 or blur < 0:
        raise RuntimeError(f"shadow 偏移/模糊不能为负: dx={dx} dy={dy} blur={blur}")
    return Shadow(dx, dy, blur, util.parse_ass_color(color))


def _bg_ass_color(color: str) -> str:
    """ASS box 由本体与 1px 偏移副本两层叠加，alpha 做平方根校正后视觉效果与配置一致。"""
    ass = util.parse_ass_color(color)
    alpha = round(255 * math.sqrt(int(ass[2:4], 16) / 255))
    return f"&H{alpha:02X}{ass[4:10]}"


def _parse_bg(style: dict) -> Bg:
    value = style.get("bg")
    if value is None or value == "" or value is False:
        return Bg(False, 0.0, 0.0, "")
    if value is True:
        value = {}
    if not isinstance(value, dict):
        raise RuntimeError(f"bg 配置非法: {value!r}")
    padding = float(value.get("padding", _DEFAULT_BG["padding"]))
    px = float(value.get("padding_x", padding))
    py = float(value.get("padding_y", padding))
    return Bg(True, px, py, _bg_ass_color(value.get("color", _DEFAULT_BG["color"])))


def _resolve_font(name: str, bold: bool, italic: bool) -> tuple[str, bool, bool]:
    family, font_style, weight, pattern_italic = util.parse_font_pattern(name)
    flag_bold, flag_italic = util.pattern_flags(font_style, weight, pattern_italic)
    bold = bold or flag_bold
    italic = italic or flag_italic
    if font_style:
        full = f"{family} {font_style}".strip()
        return (full if util.font_available(full) else family), bold, italic
    return family, bold, italic


def _role_style(style: dict, role: str, log: logging.Logger) -> tuple[str, bool, bool]:
    font = style.get(f"{role}_font_name") or style["primary_font_name"]
    bold = bool(style.get(f"{role}_bold", False))
    italic = bool(style.get(f"{role}_italic", False))
    if style.get(f"{role}_italic_shear") is not None:
        italic = False
    resolved, bold, italic = _resolve_font(font, bold, italic)
    if not util.font_available(resolved):
        log.warning("字体 %r 不存在，回退 %r", resolved, "sans-serif")
        resolved = "sans-serif"
    return resolved, bold, italic


def _role_color(style: dict, role: str) -> str:
    if role == "secondary":
        return util.parse_ass_color(style.get("secondary_color") or style["primary_color"])
    return util.parse_ass_color(style["primary_color"])


def _style_line(
    name: str,
    font: str,
    size: int,
    color: str,
    style: dict,
    shadow: Shadow,
    bold: bool,
    italic: bool,
    margin_h: int,
    margin_v: int,
    box: Bg,
) -> str:
    if box.enabled:
        outline_color = back_color = box.color
        border_style = 3
        outline = box.padding_y * size
        shadow_field = 1
    else:
        outline_color = util.parse_ass_color(style["outline_color"])
        back_color = shadow.color
        border_style = 1
        outline = float(style["outline"])
        shadow_field = 0 if (shadow.blur > 0 or shadow.dx != shadow.dy) else shadow.dx
    return (
        f"Style: {name},{font},{size},{color},&H000000FF,{outline_color},{back_color},"
        f"{int(bold)},{int(italic)},0,0,100,100,0,0,{border_style},{_fmt(outline)},{_fmt(shadow_field)},"
        f"2,{margin_h},{margin_h},{margin_v},1"
    )


def _bord_tag(px: float, py: float) -> str:
    return f"\\bord{_fmt(px)}" if px == py else f"\\xbord{_fmt(px)}\\ybord{_fmt(py)}"


def _split_ass_color(color: str) -> tuple[str, str]:
    return "&H" + color[4:10], "&H" + color[2:4]


# ---------- ASS 组装 ----------


def build_ass(zh: list[dict], en: list[dict], style: dict, probe: dict, log: logging.Logger) -> str:
    width, height = probe["width"], probe["height"]
    mode = str(style.get("mode", "bilingual")).lower()
    if mode not in ("bilingual", "mono"):
        raise RuntimeError(f"mode 配置非法: {mode!r}（bilingual | mono）")

    fontsize = max(12, round(height * float(style["font_size_ratio"])))
    margin_v = round(height * float(style["margin_v_ratio"]))
    margin_h = round(width * float(style["margin_h_ratio"]))
    secondary_size = max(10, round(fontsize * float(style["secondary_font_ratio"])))

    shadow = _parse_shadow(style.get("shadow"))
    box = _parse_bg(style)
    p_font, p_bold, p_italic = _role_style(style, "primary", log)
    s_font, s_bold, s_italic = _role_style(style, "secondary", log)
    p_color, s_color = _role_color(style, "primary"), _role_color(style, "secondary")
    p_shear = style.get("primary_italic_shear")
    s_shear = style.get("secondary_italic_shear")

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        + _style_line("Default", p_font, fontsize, p_color, style, shadow, p_bold, p_italic, margin_h, margin_v, box)
        + "\n"
    )
    if mode == "bilingual":
        header += _style_line(
            "Secondary", s_font, secondary_size, s_color, style, shadow, s_bold, s_italic, margin_h, margin_v, box
        ) + "\n"
    header += "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

    lines = [header]
    for index, cue in enumerate(zh):
        primary = _ass_escape(cue["text"]).replace("\n", "\\N")
        secondary = ""
        if mode == "bilingual" and index < len(en):
            secondary = _ass_escape(en[index]["text"]).replace("\n", "\\N")

        if box.enabled:
            lead = f"{{{_bord_tag(box.padding_x * fontsize, box.padding_y * fontsize)}}}"
            if p_shear is not None:
                lead += f"{{\\fax{p_shear}}}"
            primary = lead + primary
            text = primary
            if secondary:
                second_lead = f"{{{_bord_tag(box.padding_x * secondary_size, box.padding_y * secondary_size)}}}"
                if s_shear is not None:
                    second_lead += f"{{\\fax{s_shear}}}"
                text += f"\\N{{\\rSecondary}}{second_lead}{secondary}"
            lines.append(f"Dialogue: 0,{util.fmt_ass_ts(cue['start'])},{util.fmt_ass_ts(cue['end'])},Default,,0,0,0,,{text}")
            continue

        if p_shear is not None:
            primary = f"{{\\fax{p_shear}}}{primary}"
        if secondary and s_shear is not None:
            secondary = f"{{\\fax{s_shear}}}{secondary}"

        if shadow.blur > 0:
            color, alpha = _split_ass_color(shadow.color)
            body = primary + (f"\\N{{\\rSecondary}}{secondary}" if secondary else "")
            shadow_layer = (
                f"{{\\pos({width / 2:.1f},{height - margin_v + shadow.dy:.1f})\\bord0"
                f"\\1c{color}\\1a{alpha}\\blur{_fmt(shadow.blur)}}}{body}"
            )
            text_layer = f"{{\\pos({width / 2:.1f},{height - margin_v:.1f})}}{body}"
            lines.append(f"Dialogue: 0,{util.fmt_ass_ts(cue['start'])},{util.fmt_ass_ts(cue['end'])},Default,,0,0,0,,{shadow_layer}")
            lines.append(f"Dialogue: 0,{util.fmt_ass_ts(cue['start'])},{util.fmt_ass_ts(cue['end'])},Default,,0,0,0,,{text_layer}")
            continue

        text = primary
        if shadow.dx != shadow.dy:
            tag = f"\\xshad{_fmt(shadow.dx)}\\yshad{_fmt(shadow.dy)}"
            text = f"{{{tag}}}{primary}"
            if secondary:
                text += f"\\N{{\\rSecondary{tag}}}{secondary}"
        elif secondary:
            text += f"\\N{{\\rSecondary}}{secondary}"
        lines.append(f"Dialogue: 0,{util.fmt_ass_ts(cue['start'])},{util.fmt_ass_ts(cue['end'])},Default,,0,0,0,,{text}")

    return "\n".join(lines) + "\n"


# ---------- 烧录 ----------


def run(video: Path, cfg: Config, output: Path | None = None) -> Path:
    log = util.setup_logging()
    video = video.expanduser().resolve()
    if not video.exists():
        raise FileNotFoundError(f"视频不存在: {video}")
    zh_path = video.with_name(f"{video.stem}.zh.srt")
    en_path = video.with_name(f"{video.stem}.en.srt")
    for path in (zh_path, en_path):
        if not path.exists():
            raise FileNotFoundError(f"缺少字幕文件: {path}")

    zh, en = read_srt(zh_path), read_srt(en_path)
    if len(zh) != len(en):
        raise RuntimeError(f"中英字幕条数不一致（{len(zh)} / {len(en)}），请重新生成字幕")
    if not zh:
        raise RuntimeError(f"字幕为空: {zh_path}")

    probe = util.probe_video(video)
    style = {**STYLE_DEFAULTS, **cfg.style}
    ass = build_ass(zh, en, style, probe, log)

    encoder = cfg.burn.get("encoder", "libx264")
    if encoder not in ENCODERS:
        raise RuntimeError(f"不支持的编码器: {encoder}（可选 {', '.join(ENCODERS)}）")
    default_preset, default_crf = ENCODERS[encoder]
    crf = cfg.burn.get("crf", default_crf)
    preset = cfg.burn.get("preset", default_preset)

    output = (output or video.with_name(f"{video.stem}.burned.mp4")).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        ass_path = Path(tmp) / "subs.ass"
        ass_path.write_text(ass, encoding="utf-8")
        quoted = str(ass_path).replace("\\", "\\\\").replace("'", "\\'")
        audio = ["-c:a", "copy"] if probe["audio_codec"] in ("aac", "mp3") else ["-c:a", "aac", "-b:a", "192k"]
        log.info("烧录 %d 条字幕（%s, crf=%s, preset=%s）", len(zh), encoder, crf, preset)
        util.run_cmd(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(video),
                "-vf", f"ass=filename='{quoted}'",
                "-c:v", encoder, "-crf", str(crf), "-preset", str(preset),
                "-pix_fmt", "yuv420p", *audio,
                str(output),
            ],
            log,
        )
    log.info("输出: %s", output)
    return output
