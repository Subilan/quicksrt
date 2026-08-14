"""burn：ASS 时间戳/转义/style 块/语言模式生成（纯函数）。"""

import pytest

from quicksrt.steps.burn import (
    _ass_escape,
    _ass_ts,
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
        "font_name": "F", "font_size_ratio": 0.05,
        "primary_color": "&H00FFFFFF", "outline_color": "&H00000000",
        "outline": 2, "shadow": 1,
    }
    s = _style_block(1920, 1080, style, 54, 40, 20)
    assert s.startswith("Style: Default,F,54,&H00FFFFFF,&H000000FF,")
    assert ",0,0,0,0,100,100,0,0,1,2,1,2,20,20,40,1" in s


def test_style_block_bold_italic_custom_font():
    style = {"font_name": "F", "outline": 2, "shadow": 1}
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
        "font_name": "F1", "font_bold": True, "font_italic": False,
        "en_font_name": "F2", "en_bold": False, "en_italic": True,
    }
    assert _lang_style(style, "zh") == ("F1", True, False)
    assert _lang_style(style, "en") == ("F2", False, True)
    assert _lang_style({}, "en")[0] == "Noto Sans CJK SC"  # en 字体缺省回退主字体


# ---------- build_ass_items ----------

_STYLE = {
    "font_name": "ZH-Font", "font_size_ratio": 0.05, "margin_v_ratio": 0.05,
    "primary_color": "&H00FFFFFF", "outline_color": "&H00000000",
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
    style = {**_STYLE, "font_bold": True, "font_italic": False, "en_bold": False, "en_italic": True}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    assert "Style: Default,ZH-Font,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0" in ass
    assert "Style: Secondary,EN-Font,32,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,1,0,0" in ass
