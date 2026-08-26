"""burn：ASS 时间戳/转义/style 块/语言模式生成（纯函数）。"""

import pytest

from quicksrt.steps.burn import (
    _ass_escape,
    _ass_ts,
    _bg_color_parts,
    _bg_dialogue,
    _lang_color,
    _lang_shear,
    _lang_style,
    _style_block,
    _style_mode,
    build_ass_items,
)


@pytest.mark.parametrize(
    ("seconds", "expect"),
    [
        (0.0, "0:00:00.00"),
        (1.234, "0:00:01.23"),   # 厘秒截断
        (59.995, "0:01:00.00"),  # 进位
        (3600.0, "1:00:00.00"),
        (-1.0, "0:00:00.00"),    # 负值钳到 0
    ],
)
def test_ass_ts(seconds, expect):
    assert _ass_ts(seconds) == expect


@pytest.mark.parametrize(
    ("text", "expect"),
    [
        ("plain", "plain"),
        (r"a\b", r"a\\b"),
        ("{a}", r"\{a\}"),
        (r"{\i1}x\N", r"\{\\i1\}x\\N"),
    ],
)
def test_ass_escape(text, expect):
    assert _ass_escape(text) == expect


def test_style_block():
    style = {
        "zh_font_name": "F", "font_size_ratio": 0.05,
        "zh_color": "&H00FFFFFF", "outline_color": "&H00000000",
        "outline": 2, "shadow": 1,
    }
    s = _style_block(1920, 1080, style, 54, 40, 20)
    assert s.startswith("Style: Default,F,54,&H00FFFFFF,&H000000FF,")
    assert ",0,0,0,0,100,100,0,0,1,2,1,2,20,20,40,1" in s


def test_style_block_bold_italic_custom_font():
    style = {"zh_font_name": "F", "outline": 2, "shadow": 1}
    s = _style_block(1920, 1080, style, 54, 40, 20, "Secondary", "EN", True, True)
    assert s.startswith("Style: Secondary,EN,54,")
    assert ",1,1,0,0,100,100" in s


# ---------- _style_mode / _lang_style ----------

def test_style_mode_defaults():
    assert _style_mode({}) == ("bilingual", "zh")


def test_style_mode_explicit():
    assert _style_mode({"mode": "mono", "primary_lang": "en"}) == ("mono", "en")


def test_style_mode_legacy_bilingual_bool():
    assert _style_mode({"bilingual": True}) == ("bilingual", "zh")
    assert _style_mode({"bilingual": False}) == ("mono", "zh")


def test_style_mode_invalid_falls_back():
    assert _style_mode({"mode": "weird", "primary_lang": "fr"}) == ("bilingual", "zh")


def test_lang_style():
    style = {
        "zh_font_name": "F1", "zh_faux_bold": True, "zh_faux_italic": False,
        "en_font_name": "F2", "en_faux_bold": False, "en_faux_italic": True,
    }
    assert _lang_style(style, "zh") == ("F1", True, False)
    assert _lang_style(style, "en") == ("F2", False, True)
    assert _lang_style({}, "en")[0] == "Noto Sans CJK SC"  # en 字体缺省回退主字体


def test_lang_shear():
    assert _lang_shear({}, "zh") is None
    assert _lang_shear({"zh_italic_shear": ""}, "zh") is None
    assert _lang_shear({"zh_italic_shear": 0.2}, "zh") == 0.2
    assert _lang_shear({"en_italic_shear": "-0.15"}, "en") == "-0.15"


def test_lang_style_shear_disables_italic_flag():
    """设置 italic_shear 时用 \\fax 剪切，Italic 标志关闭（避免双重倾斜）。"""
    style = {"zh_font_name": "F", "zh_faux_italic": True, "zh_italic_shear": 0.2}
    assert _lang_style(style, "zh") == ("F", False, False)
    style_en = {"en_font_name": "F2", "en_faux_italic": True, "en_italic_shear": "-0.1"}
    assert _lang_style(style_en, "en") == ("F2", False, False)


def test_lang_color():
    style = {"zh_color": "&H00FFFFFF", "en_color": "&H00F39621"}
    assert _lang_color(style, "zh") == "&H00FFFFFF"
    assert _lang_color(style, "en") == "&H00F39621"
    # en_color 缺省/留空时英文回退 zh_color
    assert _lang_color({"zh_color": "&H00FFFFFF"}, "en") == "&H00FFFFFF"
    assert _lang_color({"zh_color": "&H00FFFFFF", "en_color": ""}, "en") == "&H00FFFFFF"
    assert _lang_color({}, "zh") == "&H00FFFFFF"


# ---------- build_ass_items ----------

_STYLE = {
    "zh_font_name": "ZH-Font", "font_size_ratio": 0.05, "margin_v_ratio": 0.05,
    "zh_color": "&H00FFFFFF", "outline_color": "&H00000000",
    "outline": 2, "shadow": 1,
    "en_font_name": "EN-Font", "en_font_ratio": 0.6,
}
_ITEMS = [{"id": 0, "src_id": 0, "start": 1.0, "end": 2.0, "zh": "你好", "en": "hello"}]
_PROBE = {"width": 1920, "height": 1080}


def test_build_ass_bilingual_zh_primary():
    ass = build_ass_items(_ITEMS, _STYLE, _PROBE)
    assert "Style: Default,ZH-Font,54," in ass
    assert "Style: Secondary,EN-Font,32," in ass
    assert "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,你好\\N{\\rSecondary}hello" in ass


def test_build_ass_en_color():
    """en_color 只作用于英文样式，中文保持 zh_color。"""
    style = {**_STYLE, "en_color": "&H00F39621"}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    assert "Style: Default,ZH-Font,54,&H00FFFFFF," in ass      # 中文白
    assert "Style: Secondary,EN-Font,32,&H00F39621," in ass    # 英文蓝
    # 英文为主语言时：Default 用 en_color
    ass_en = build_ass_items(_ITEMS, style, _PROBE, mode="bilingual", primary_lang="en")
    assert "Style: Default,EN-Font,54,&H00F39621," in ass_en
    assert "Style: Secondary,ZH-Font,32,&H00FFFFFF," in ass_en


def test_build_ass_bilingual_en_primary():
    ass = build_ass_items(_ITEMS, _STYLE, _PROBE, mode="bilingual", primary_lang="en")
    assert "Style: Default,EN-Font,54," in ass
    assert "Style: Secondary,ZH-Font,32," in ass
    assert "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hello\\N{\\rSecondary}你好" in ass


def test_build_ass_mono_zh():
    ass = build_ass_items(_ITEMS, _STYLE, _PROBE, mode="mono")
    assert "Style: Default,ZH-Font,54," in ass
    assert "Style: Secondary" not in ass
    assert "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,你好" in ass


def test_build_ass_mono_en():
    ass = build_ass_items(_ITEMS, _STYLE, _PROBE, mode="mono", primary_lang="en")
    assert "Style: Default,EN-Font,54," in ass
    assert "Style: Secondary" not in ass
    assert "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hello" in ass


def test_build_ass_bold_italic():
    style = {**_STYLE, "zh_faux_bold": True, "zh_faux_italic": False, "en_faux_bold": False, "en_faux_italic": True}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    assert "Style: Default,ZH-Font,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0" in ass
    assert "Style: Secondary,EN-Font,32,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,1,0,0" in ass


def test_build_ass_italic_shear():
    """italic_shear 自定义倾角：Italic 标志关，文本加 {\\fax} 剪切。"""
    style = {**_STYLE, "zh_italic_shear": 0.2, "en_italic_shear": "-0.15"}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    assert "Style: Default,ZH-Font,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0" in ass
    assert "Style: Secondary,EN-Font,32,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0" in ass
    assert "{\\fax0.2}你好\\N{\\rSecondary}{\\fax-0.15}hello" in ass


# ---------- 字幕背景（全宽半透明矩形条） ----------

def test_bg_color_parts():
    assert _bg_color_parts("&H80000000") == ("&H000000", "&H80")
    assert _bg_color_parts("&H33FF0000") == ("&HFF0000", "&H33")  # BBGGRR 蓝 + alpha 33


def test_build_ass_bg_dialogue_emitted_first():
    style = {**_STYLE, "bg_enabled": True, "bg_color": "&H80000000", "bg_padding_ratio": 0.35}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    assert ass.count("Dialogue: 0,") == 2  # 背景 + 文本
    bg, text = [l for l in ass.splitlines() if l.startswith("Dialogue: 0,")]
    assert "\\p1" in bg and "\\p0" in bg
    assert "\\an2\\pos(" in bg
    assert "\\1c&H000000&\\1a&H80&" in bg
    assert "\\3a&HFF&\\4a&HFF&" in bg  # 描边/阴影透明
    assert bg.startswith("Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,")
    assert "你好\\N{\\rSecondary}hello" in text  # 文本行不受影响


def test_build_ass_bg_disabled_by_default():
    ass = build_ass_items(_ITEMS, _STYLE, _PROBE)
    assert ass.count("Dialogue: 0,") == 1
    assert "\\p1" not in ass


def test_bg_dialogue_geometry_mono_single_line():
    style = {**_STYLE, "bg_padding_ratio": 0.35}
    it = {"id": 0, "src_id": 0, "start": 0.0, "end": 1.0, "zh": "你好", "en": "hi"}
    bg = _bg_dialogue(it, 1920, 1080, 54, 32, 54, 58, style, "mono", "zh")
    # 块高 = 0.8*54 + 2*0.35*54 = 81；块底 = 1080-54-0.167*54+0.35*54 ≈ 1035.88
    assert "m 0 0 l 1920.00 0 l 1920.00 81.00 l 0 81.00 l 0 0" in bg
    assert "\\pos(960.0,1035.88)" in bg


def test_bg_dialogue_geometry_bilingual():
    style = {**_STYLE, "bg_padding_ratio": 0.35}
    it = {"id": 0, "src_id": 0, "start": 0.0, "end": 1.0, "zh": "你好", "en": "hi"}
    bg = _bg_dialogue(it, 1920, 1080, 54, 32, 54, 58, style, "bilingual", "zh")
    # 块高 = 54(主行) + 0.8*32(末行英文) + 2*18.9 = 117.4；块底 = 1080-54-0.167*32+18.9 ≈ 1039.56
    assert "l 1920.00 117.40 l 0 117.40" in bg
    assert "\\pos(960.0,1039.56)" in bg
