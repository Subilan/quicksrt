"""burn：ASS 时间戳/转义/style 块/语言模式生成（纯函数）。"""

import pytest

from quicksrt.steps.burn import (
    _ass_escape,
    _ass_ts,
    _bg_ass_color,
    _ensure_font,
    _fmt,
    _lang_color,
    _lang_shear,
    _lang_style,
    _parse_shadow,
    _resolve_font,
    _style_block,
    _style_mode,
    build_ass,
    build_ass_items,
)


@pytest.fixture(autouse=True)
def _no_font_check(monkeypatch):
    """默认让测试里的假字体名通过校验；字体校验行为由专项用例覆盖。"""
    import quicksrt.util as util

    monkeypatch.setattr(util, "font_available", lambda name: True)


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
        "zh_color": "#FFFFFF", "outline_color": "#000000",
        "outline": 2, "shadow": 1,
    }
    s = _style_block(1920, 1080, style, 54, 40, 20)
    assert s.startswith("Style: Default,F,54,&H00FFFFFF,&H000000FF,")
    assert ",0,0,0,0,100,100,0,0,1,2,1,2,20,20,40,1" in s


def test_style_block_css_color():
    """CSS 颜色经统一解析后写入 ASS：rgb()/rgba()/#HEX 均可用。"""
    style = {"zh_font_name": "F", "zh_color": "rgb(255, 0, 0)",
             "outline_color": "rgba(0, 255, 0, 1)", "outline": 2, "shadow": 1}
    s = _style_block(1920, 1080, style, 54, 40, 20)
    assert s.startswith("Style: Default,F,54,&H000000FF,&H000000FF,")  # red, BBGGRR
    assert ",&H0000FF00," in s  # green outline


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
        "zh_font_name": "F1", "zh_bold": True, "zh_italic": False,
        "en_font_name": "F2", "en_bold": False, "en_italic": True,
    }
    assert _lang_style(style, "zh") == ("F1", True, False)
    assert _lang_style(style, "en") == ("F2", False, True)
    assert _lang_style({}, "en")[0] == "sans-serif"  # en 字体缺省回退主字体


# ---------- 字体缺失校验与默认字体回退 ----------


def test_ensure_font_available_unchanged(caplog, monkeypatch):
    import quicksrt.util as util

    monkeypatch.setattr(util, "font_available", lambda name: True)
    with caplog.at_level("WARNING", logger="quicksrt"):
        assert _ensure_font("MyFont", "primary(zh)") == "MyFont"
    assert not caplog.records


def test_ensure_font_missing_falls_back(caplog, monkeypatch):
    import quicksrt.util as util

    monkeypatch.setattr(util, "font_available", lambda name: name != "GhostFont")
    with caplog.at_level("WARNING", logger="quicksrt"):
        assert _ensure_font("GhostFont", "primary(zh)") == "sans-serif"
    assert len(caplog.records) == 1
    assert "GhostFont" in caplog.text and "回退到默认字体" in caplog.text


def test_ensure_font_missing_and_default_missing(caplog, monkeypatch):
    import quicksrt.util as util

    monkeypatch.setattr(util, "font_available", lambda name: False)
    with caplog.at_level("WARNING", logger="quicksrt"):
        assert _ensure_font("GhostFont", "zh") == "GhostFont"  # 默认字体也不存在时保持原名
    assert len(caplog.records) == 2


def test_build_ass_items_font_missing_warns(caplog, monkeypatch):
    """配置字体缺失时：ASS 使用默认字体且输出警告。"""
    import quicksrt.util as util

    monkeypatch.setattr(util, "font_available", lambda name: name != "ZH-Font")
    style = {**_STYLE, "zh_font_name": "ZH-Font"}
    with caplog.at_level("WARNING", logger="quicksrt"):
        ass = build_ass_items(_ITEMS, style, _PROBE)
    assert "Style: Default,sans-serif,54," in ass
    assert "ZH-Font" in caplog.text and "回退到默认字体" in caplog.text


def test_lang_shear():
    assert _lang_shear({}, "zh") is None
    assert _lang_shear({"zh_italic_shear": ""}, "zh") is None
    assert _lang_shear({"zh_italic_shear": 0.2}, "zh") == 0.2
    assert _lang_shear({"en_italic_shear": "-0.15"}, "en") == "-0.15"


def test_lang_style_shear_disables_italic_flag():
    """设置 italic_shear 时用 \\fax 剪切，Italic 标志关闭（避免双重倾斜）。"""
    style = {"zh_font_name": "F", "zh_italic": True, "zh_italic_shear": 0.2}
    assert _lang_style(style, "zh") == ("F", False, False)
    style_en = {"en_font_name": "F2", "en_italic": True, "en_italic_shear": "-0.1"}
    assert _lang_style(style_en, "en") == ("F2", False, False)


# ---------- 字体名解析（fontconfig 模式 -> ASS 字体名） ----------


def test_resolve_font_plain_name_unchanged():
    assert _resolve_font("IBM Plex Sans", False, False) == ("IBM Plex Sans", False, False)
    assert _resolve_font("STHeitiSC-Medium", False, False) == ("STHeitiSC-Medium", False, False)


def test_resolve_font_pattern_to_fullname():
    """":style=" 写法转 "Family Style"（字体全名形式，libass 精确匹配）。"""
    assert _resolve_font("Heiti SC:style=Medium", False, False) == ("Heiti SC Medium", False, False)


def test_resolve_font_pattern_no_fullname_falls_back_family(monkeypatch):
    """拼接的全名在系统中不存在时退回 family 名，避免整串匹配失败。"""
    import quicksrt.util as util

    monkeypatch.setattr(util, "font_available", lambda name: name == "Some Family")
    assert _resolve_font("Some Family:style=Weird", False, False) == ("Some Family", False, False)


def test_resolve_font_pattern_weight_slant_flags():
    """weight/slant 映射粗体/斜体标志，与显式标志合并。"""
    assert _resolve_font("F:weight=700", False, False) == ("F", True, False)
    assert _resolve_font("F:slant=italic", False, False) == ("F", False, True)
    assert _resolve_font("F:style=Bold", False, False) == ("F Bold", True, False)
    assert _resolve_font("F:style=Medium", True, False) == ("F Medium", True, False)  # 显式粗体保留


def test_build_ass_items_font_pattern_in_ass():
    """配置写 fontconfig 模式时，ASS 样式表用 "Family Style" 字体名。"""
    style = {**_STYLE, "zh_font_name": "Heiti SC:style=Medium"}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    assert "Style: Default,Heiti SC Medium,54," in ass
    assert "Style: Secondary,EN-Font,32," in ass


# ---------- 编码器选择 ----------


@pytest.mark.parametrize(
    ("burn_cfg", "cli_encoder", "video_codec", "expect"),
    [
        ({}, None, "h264", "libx264"),              # 自动按源编码器
        ({}, None, "hevc", "libx265"),
        ({}, None, "av1", "libsvtav1"),
        ({}, None, "vp9", "libx264"),               # 未知编码器回退 libx264
        ({"encoder": "libsvtav1"}, None, "h264", "libsvtav1"),   # config 优先于自动
        ({}, "libx265", "h264", "libx265"),        # CLI 优先于 config
        ({"encoder": "libsvtav1"}, "libx265", "h264", "libx265"),
        ({"encoder": "  "}, None, "hevc", "libx265"),           # 空白视为未设置
    ],
)
def test_resolve_encoder(burn_cfg, cli_encoder, video_codec, expect):
    from quicksrt.steps.burn import _resolve_encoder

    assert _resolve_encoder(burn_cfg, cli_encoder, video_codec) == expect


def test_resolve_encoder_invalid():
    from quicksrt.steps.burn import _resolve_encoder

    with pytest.raises(RuntimeError, match="不支持的编码器"):
        _resolve_encoder({"encoder": "libx999"}, None, "h264")


def test_lang_color():
    style = {"zh_color": "#FFFFFF", "en_color": "rgba(33, 150, 243, 1)"}
    assert _lang_color(style, "zh") == "&H00FFFFFF"
    assert _lang_color(style, "en") == "&H00F39621"
    # en_color 缺省/留空时英文回退 zh_color
    assert _lang_color({"zh_color": "#FFFFFF"}, "en") == "&H00FFFFFF"
    assert _lang_color({"zh_color": "#FFFFFF", "en_color": ""}, "en") == "&H00FFFFFF"
    assert _lang_color({}, "zh") == "&H00FFFFFF"


def test_lang_color_hex_alpha():
    """#RRGGBBAA：末尾 AA 直接作为 ASS alpha 字节（00=不透明，FF=全透明）。"""
    assert _lang_color({"zh_color": "#000000FF"}, "zh") == "&HFF000000"
    assert _lang_color({"zh_color": "#00000080"}, "zh") == "&H80000000"


# ---------- build_ass_items ----------

_STYLE = {
    "zh_font_name": "ZH-Font", "font_size_ratio": 0.05, "margin_v_ratio": 0.05,
    "zh_color": "#FFFFFF", "outline_color": "#000000",
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
    style = {**_STYLE, "en_color": "#2196F3"}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    assert "Style: Default,ZH-Font,54,&H00FFFFFF," in ass      # 中文白
    assert "Style: Secondary,EN-Font,32,&H00F39621," in ass    # 英文蓝（#2196F3 -> BBGGRR F39621）
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
    style = {**_STYLE, "zh_bold": True, "zh_italic": False, "en_bold": False, "en_italic": True}
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


# ---------- 字幕背景（BorderStyle=3 box，贴合文本） ----------


def test_bg_ass_color_alpha_compensation():
    """bg_color 解析 + alpha 平方根校正：libass box 为两层叠加，
    视觉不透明度 = 1-(a/255)²，校正后配置的透明度精确生效。"""
    # rgba(0,0,0,0.5) -> ASS alpha 128 -> 校正 181（视觉 50% 黑）
    assert _bg_ass_color({**{'bg_color': "rgba(0, 0, 0, 0.5)"}}) == "&HB5000000"
    # 不透明色 alpha=0 不变
    assert _bg_ass_color({**{'bg_color': "#000000"}}) == "&H00000000"
    # #RRGGBBAA 的 AA 是 ASS alpha，同样校正
    assert _bg_ass_color({**{'bg_color': "#00000080"}}) == "&HB5000000"
    # 默认值
    assert _bg_ass_color({}) == "&HB5000000"


def test_build_ass_bg_box_style():
    """bg_enabled：Default/Secondary 样式切 BorderStyle=3，
    OutlineColour=BackColour=box 色（含 alpha），Shadow=1（box 走 outline 槽），
    Outline=按字号的内边距。"""
    style = {**_STYLE, "bg_enabled": True, "bg_color": "rgba(0, 0, 0, 0.5)", "bg_padding_ratio": 0.35}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    assert "Style: Default,ZH-Font,54,&H00FFFFFF,&H000000FF,&HB5000000,&HB5000000,0,0,0,0,100,100,0,0,3,18.9,1,2" in ass
    assert "Style: Secondary,EN-Font,32,&H00FFFFFF,&H000000FF,&HB5000000,&HB5000000,0,0,0,0,100,100,0,0,3,11.2,1,2" in ass
    assert ass.count("Dialogue: 0,") == 1  # 无独立背景 Dialogue


def test_build_ass_bg_inline_bord():
    """文本 Dialogue 内联 \\bord 按各语言字号设置 box 内边距（主 18.9 / 副 11.2）。"""
    style = {**_STYLE, "bg_enabled": True, "bg_padding_ratio": 0.35}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    dl = next(l for l in ass.splitlines() if l.startswith("Dialogue: 0,"))
    assert "{\\bord18.9}你好\\N{\\rSecondary}{\\bord11.2}hello" in dl


def test_build_ass_bg_mono():
    """mono 模式：单 \\bord，无副语言段。"""
    style = {**_STYLE, "bg_enabled": True, "bg_padding_ratio": 0.35}
    ass = build_ass_items(_ITEMS, style, _PROBE, mode="mono")
    assert "Style: Secondary" not in ass
    dl = next(l for l in ass.splitlines() if l.startswith("Dialogue: 0,"))
    assert "{\\bord18.9}你好" in dl


def test_build_ass_bg_with_shear():
    """bg + italic_shear：\\bord 与 \\fax 合并进同一 override。"""
    style = {**_STYLE, "bg_enabled": True, "zh_italic_shear": 0.2, "en_italic_shear": "-0.15"}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    dl = next(l for l in ass.splitlines() if l.startswith("Dialogue: 0,"))
    assert "{\\bord18.9\\fax0.2}你好\\N{\\rSecondary}{\\bord11.2\\fax-0.15}hello" in dl


def test_build_ass_bg_ignores_shadow():
    """bg 模式覆盖 shadow：不输出阴影层/模糊层，文本行只有一条。"""
    style = {**_STYLE, "bg_enabled": True, "shadow": {"dx": 2, "dy": 3, "blur": 2}}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    assert ass.count("Dialogue: 0,") == 1
    assert "\\blur" not in ass and "\\pos(" not in ass


def test_build_ass_bg_disabled_by_default():
    ass = build_ass_items(_ITEMS, _STYLE, _PROBE)
    assert ass.count("Dialogue: 0,") == 1
    assert "BorderStyle=3" not in ass.replace("\n", "").replace(" ", "")  # 默认 BorderStyle=1
    assert "\\bord" not in ass


def test_build_ass_srt_bg(tmp_path):
    """build_ass（srt 直烧路径）同样支持 box 背景。"""
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n你好\n\n", encoding="utf-8")
    style = {**_STYLE, "bg_enabled": True, "bg_padding_ratio": 0.35}
    ass = build_ass(srt, style, _PROBE)
    assert "Style: Default,ZH-Font,54,&H00FFFFFF,&H000000FF,&HB5000000,&HB5000000,0,0,0,0,100,100,0,0,3,18.9,1,2" in ass
    assert "{\\bord18.9}你好" in ass


# ---------- 阴影重构（shadow 偏移/模糊/颜色） ----------


def test_fmt():
    assert _fmt(1) == "1"
    assert _fmt(1.0) == "1"
    assert _fmt(0) == "0"
    assert _fmt(2.5) == "2.5"


def test_parse_shadow_unset():
    s = _parse_shadow(None)
    assert (s.dx, s.dy, s.blur, s.color) == (0, 0, 0, "&H80000000")
    assert _parse_shadow("").dx == 0


def test_parse_shadow_number_legacy():
    """旧式数字：dx=dy=N、无模糊、默认半透明黑。"""
    s = _parse_shadow(1)
    assert (s.dx, s.dy, s.blur) == (1, 1, 0)
    assert s.color == "&H80000000"  # rgba(0,0,0,0.5) -> alpha 0x80
    s = _parse_shadow(2.5)
    assert (s.dx, s.dy) == (2.5, 2.5)


def test_parse_shadow_table():
    s = _parse_shadow({"dx": 2, "dy": 3, "blur": 2, "color": "rgba(0, 0, 0, 0.6)"})
    assert (s.dx, s.dy, s.blur) == (2, 3, 2)
    assert s.color == "&H66000000"  # 0.6 -> alpha 0x66
    # 部分键缺省：dx/dy 缺省 1、blur 缺省 0、color 缺省半透明黑
    s = _parse_shadow({"color": "#000000FF"})
    assert (s.dx, s.dy, s.blur) == (1, 1, 0)
    assert s.color == "&HFF000000"


def test_parse_shadow_invalid():
    with pytest.raises(RuntimeError, match="shadow 配置非法"):
        _parse_shadow("2")
    with pytest.raises(RuntimeError, match="shadow 配置非法"):
        _parse_shadow(True)
    with pytest.raises(RuntimeError, match="不能为负"):
        _parse_shadow({"dx": -1})
    with pytest.raises(RuntimeError, match="shadow.color"):
        _parse_shadow({"color": "not-a-color"})


def test_style_block_shadow_table_equal_offset():
    """dx==dy 时 Shadow 字段写标量偏移，BackColour 写阴影色。"""
    style = {"zh_font_name": "F", "outline": 2, "shadow": {"dx": 2, "dy": 2, "color": "#000000FF"}}
    s = _style_block(1920, 1080, style, 54, 40, 20)
    assert ",&HFF000000," in s  # BackColour = 不透明黑
    assert ",1,2,2,2,20,20,40,1" in s  # BorderStyle=1, Outline=2, Shadow=2, Alignment=2


def test_style_block_shadow_table_xy_diff():
    """dx≠dy 时 Shadow 字段写 0（内联 \\xshad/\\yshad 控制）。"""
    style = {"zh_font_name": "F", "outline": 2, "shadow": {"dx": 1, "dy": 3}}
    s = _style_block(1920, 1080, style, 54, 40, 20)
    assert ",1,2,0,2,20,20,40,1" in s


def test_style_block_shadow_table_blur():
    """blur>0 双层渲染时 Shadow 字段写 0（阴影层控制）。"""
    style = {"zh_font_name": "F", "outline": 2, "shadow": {"blur": 2}}
    s = _style_block(1920, 1080, style, 54, 40, 20)
    assert ",1,2,0,2,20,20,40,1" in s


def test_build_ass_two_layer_shadow():
    """blur>0：每条字幕渲染阴影层 + 文本层；阴影层偏移/模糊/阴影色，文本层保持原样式。"""
    style = {**_STYLE, "shadow": {"dx": 2, "dy": 3, "blur": 2, "color": "rgba(0, 0, 0, 0.6)"}}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    dls = [l for l in ass.splitlines() if l.startswith("Dialogue: 0,")]
    assert len(dls) == 2
    sh, tx = dls
    # 阴影层：偏移 dx=2/dy=3、关描边、阴影色、模糊
    assert "{\\pos(962.0,1029.0)\\bord0\\1c&H66000000\\blur2}" in sh
    # 文本层：无偏移 pos，无 blur/bord0 覆盖（正文保持锐利）
    assert "{\\pos(960.0,1026.0)}你好\\N{\\rSecondary}hello" in tx
    assert "\\blur" not in tx and "\\bord0" not in tx
    # Style 行 Shadow=0（双层控制）
    assert "Style: Default,ZH-Font,54,&H00FFFFFF,&H000000FF,&H00000000,&H66000000,0,0,0,0,100,100,0,0,1,2,0,2" in ass


def test_build_ass_two_layer_bilingual():
    """双语双层：副语言段 \\rSecondary 后重设阴影参数（\\r 会重置样式类标签）。"""
    style = {**_STYLE, "shadow": {"dx": 2, "dy": 3, "blur": 2}}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    sh = next(l for l in ass.splitlines() if l.startswith("Dialogue: 0,") and "\\blur" in l)
    assert "你好\\N{\\rSecondary\\bord0\\1c&H80000000\\blur2}hello" in sh


def test_build_ass_single_layer_xy_diff():
    """blur=0 且 dx≠dy：内联 \\xshad/\\yshad 前缀，双语每段重设。"""
    style = {**_STYLE, "shadow": {"dx": 1, "dy": 3}}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    dls = [l for l in ass.splitlines() if l.startswith("Dialogue: 0,")]
    assert len(dls) == 1
    assert "{\\xshad1\\yshad3}你好\\N{\\rSecondary\\xshad1\\yshad3}hello" in dls[0]


def test_build_ass_single_layer_equal_offset():
    """blur=0 且 dx==dy：无内联标签，Style Shadow 字段承载偏移。"""
    style = {**_STYLE, "shadow": {"dx": 2, "dy": 2}}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    assert "\\xshad" not in ass and "\\pos(" not in ass
    assert "Style: Default,ZH-Font,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,2,2" in ass


def test_build_ass_bg_with_two_layer_shadow():
    """背景 + 双层阴影：box 背景替代阴影层，只输出文本层一条（避免双重 box）。"""
    style = {**_STYLE, "bg_enabled": True, "shadow": {"dx": 2, "dy": 3, "blur": 2}}
    ass = build_ass_items(_ITEMS, style, _PROBE)
    dls = [l for l in ass.splitlines() if l.startswith("Dialogue: 0,")]
    assert len(dls) == 1
    assert "\\blur" not in dls[0] and "\\p1" not in dls[0]
    assert "你好" in dls[0]


def test_build_ass_srt_two_layer(tmp_path):
    """SRT 直转路径：双层渲染同样生效。"""
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello World\n\n", encoding="utf-8")
    style = {**_STYLE, "shadow": {"dx": 2, "dy": 3, "blur": 2}}
    ass = build_ass(srt, style, _PROBE)
    dls = [l for l in ass.splitlines() if l.startswith("Dialogue: 0,")]
    assert len(dls) == 2
    assert "{\\pos(962.0,1029.0)\\bord0\\1c&H80000000\\blur2}Hello World" in dls[0]
    assert "{\\pos(960.0,1026.0)}Hello World" in dls[1]
    assert "Style: Default,ZH-Font,54," in ass


def test_build_ass_srt_single_layer_xy_diff(tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello World\n\n", encoding="utf-8")
    style = {**_STYLE, "shadow": {"dx": 1, "dy": 3}}
    ass = build_ass(srt, style, _PROBE)
    assert "{\\xshad1\\yshad3}Hello World" in ass
