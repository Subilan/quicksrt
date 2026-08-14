"""burn：ASS 时间戳/转义/style 块生成（纯函数）。"""

import pytest

from quicksrt.steps.burn import _ass_escape, _ass_ts, _style_block


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
