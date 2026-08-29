"""srt：时长 clamp、重叠修正、折行、渲染。"""

import pytest

from quicksrt.models import Segment
from quicksrt.steps.srt import _split_lines, normalize, render, render_bilingual


def _seg(id, start, end, text="t"):
    return Segment(id=id, start=start, end=end, text=text)


def _cfg(**kw):
    cfg = {"max_line_chars": 42, "max_lines": 2, "min_duration": 1.0, "max_duration": 0}
    cfg.update(kw)
    return cfg


# ---------- normalize ----------

def test_normalize_min_duration():
    out = normalize([_seg(0, 1.0, 1.2)], _cfg(min_duration=1.0))
    assert out[0].end == 2.0  # 0.2s 拉长到 1s


def test_normalize_max_duration():
    out = normalize([_seg(0, 1.0, 5.0)], _cfg(max_duration=2.0))
    assert out[0].end == 3.0


def test_normalize_max_duration_disabled():
    out = normalize([_seg(0, 1.0, 5.0)], _cfg(max_duration=0))
    assert out[0].end == 5.0


def test_normalize_no_overlap():
    out = normalize([_seg(0, 0.0, 1.0), _seg(1, 0.8, 2.0)], _cfg())
    assert out[0].end <= out[1].start
    assert out[0].end == 0.75  # max(start+0.1, next.start-0.05)


def test_normalize_trim_and_wrap():
    segs = [_seg(0, 0.0, 1.0, "  " + "字" * 50 + "  ")]
    out = normalize(segs, _cfg(max_line_chars=10))
    assert out[0].text == "字" * 10 + "\n" + "字" * 40
    assert len(out[0].text.splitlines()) == 2


# ---------- _split_lines ----------

def test_split_lines_short():
    assert _split_lines("hello", 10, 2) == ["hello"]


def test_split_lines_wrap_at_punct():
    out = _split_lines("第一句。第二句。第三句。", 6, 2)
    assert out == ["第一句。", "第二句。第三句。"]


def test_split_lines_no_punct():
    out = _split_lines("abcdefghijklmnopqrst", 5, 2)
    assert out == ["abcde", "fghijklmnopqrst"]


# ---------- render ----------

def test_render():
    text = render([_seg(0, 0.0, 1.234), _seg(1, 5.0, 6.0)])
    assert text == (
        "1\n00:00:00,000 --> 00:00:01,234\nt\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\nt\n"
    )


def test_render_bilingual():
    items = [{"id": 0, "zh": "你好", "en": "hello", "start": 0.0, "end": 1.0}]
    text = render_bilingual(items, "zh", "en")
    assert "你好\nhello" in text
    assert text.startswith("1\n00:00:00,000 --> 00:00:01,000")


def test_render_bilingual_any_lang_pair():
    """任意语言组合：按 primary/secondary 语言码取字段、决定上下顺序。"""
    items = [{"id": 0, "ja": "こんにちは", "en": "hello", "start": 0.0, "end": 1.0}]
    text = render_bilingual(items, "ja", "en")
    assert "こんにちは\nhello" in text
    # 主副互换：顺序反转
    text2 = render_bilingual(items, "en", "ja")
    assert "hello\nこんにちは" in text2
