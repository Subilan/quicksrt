"""refine：标点清理、拆句、副语言按比例同步拆分、时间分配（纯函数）。"""

import json
import logging
from pathlib import Path

import pytest

from quicksrt.models import Segment, save_segments
from quicksrt.steps.refine import (
    _inner_split,
    _FALLBACK_RULE,
    assign_time,
    lang_rule,
    split_by_ratio,
    split_text,
    strip_end_punct,
)

ZH = lang_rule("zh")
EN = lang_rule("en")
JA = lang_rule("ja")


# ---------- lang_rule ----------

def test_lang_rule_builtin():
    assert ZH["split_punct"] == "，、；"
    assert EN["space_separator"] is True
    assert JA["break_after"]


def test_lang_rule_unknown_fallback():
    # 未知语言用通用规则（Unicode 标点 + 空格断行）
    rule = lang_rule("xx")
    assert rule == _FALLBACK_RULE


def test_lang_rule_overrides():
    rule = lang_rule("zh", {"split_punct": "。"})
    assert rule["split_punct"] == "。"
    assert rule["strip_punct"] == "。．，、；"  # 未覆盖键保留


def test_lang_rule_overrides_empty_ignored():
    rule = lang_rule("zh", {"split_punct": ""})
    assert rule["split_punct"] == "，、；"


# ---------- strip_end_punct ----------

@pytest.mark.parametrize(
    ("text", "expect"),
    [
        ("你好。", "你好"),
        ("你好，", "你好"),
        ("你好！", "你好！"),      # 语气标点保留
        ("你好？", "你好？"),
        ("你好。。", "你好"),
        ("你好。 ", "你好"),
        (" 你好。 ", " 你好"),  # 只去尾部标点/空白，不动前导
        ("你好", "你好"),
    ],
)
def test_strip_end_punct_zh(text, expect):
    assert strip_end_punct(text, ZH["strip_punct"]) == expect


def test_strip_end_punct_empty_rule_keeps():
    # en 规则 strip_punct 为空：不去句尾标点
    assert strip_end_punct("Hello.", EN["strip_punct"]) == "Hello."
    assert strip_end_punct("Hello world!", EN["strip_punct"]) == "Hello world!"


# ---------- split_text（中文规则） ----------

def test_split_zh_short():
    assert split_text("短文本", 42, ZH) == ["短文本"]


def test_split_zh_at_punct():
    out = split_text("第一句，第二句，第三句", 5, ZH)
    assert out == ["第一句，", "第二句，", "第三句"]


def test_split_zh_merge_short_pieces():
    # 贪心合并不超过 max_chars
    out = split_text("一二，三四五，六七八九", 7, ZH)
    assert all(len(p) <= 7 for p in out)
    assert "".join(out) == "一二，三四五，六七八九"


def test_split_zh_inner_split_fallback():
    # 无标点的超长句走行内断行（中文虚词）
    out = split_text("这是完全没有标点的超长句子内容没有任何逗号", 10, ZH)
    assert all(len(p) <= 10 for p in out)
    assert "".join(out) == "这是完全没有标点的超长句子内容没有任何逗号"


def test_split_zh_space_not_split_by_default():
    # 中文规则默认空格不是分句分隔符
    out = split_text("第一句，第二句 第三句", 8, ZH)
    assert out == ["第一句，", "第二句 第三句"]


def test_split_zh_space_split_override():
    text = "第一句 第二句 第三句"
    out = split_text(text, 4, ZH, split_on_space=True)
    assert out == ["第一句 ", "第二句 ", "第三句"]


def test_split_zh_space_split_fullwidth():
    out = split_text("第一句\u3000第二句", 4, ZH, split_on_space=True)
    assert out == ["第一句\u3000", "第二句"]


def test_split_zh_space_split_short():
    out = split_text("第一句 第二句", 42, ZH, split_on_space=True)
    assert out == ["第一句 第二句"]


def test_split_zh_space_merge():
    out = split_text("一二 三四 五六 七八", 7, ZH, split_on_space=True)
    assert all(len(p) <= 7 for p in out)
    assert "".join(out) == "一二 三四 五六 七八"


def test_split_zh_space_with_punct():
    out = split_text("第一句，第二句 第三句", 8, ZH, split_on_space=True)
    assert out == ["第一句，第二句 ", "第三句"]


# ---------- split_text（英文/日文/通用规则） ----------

def test_split_en_at_punct():
    out = split_text("First sentence. Second sentence. Third.", 20, EN)
    assert out == ["First sentence. ", "Second sentence. ", "Third."]


def test_split_en_by_space():
    # 英文规则 space_separator=True：无句末标点也可按空格拆
    out = split_text("hello world this is a long text", 14, EN)
    assert all(len(p) <= 14 for p in out)
    assert "".join(out) == "hello world this is a long text"


def test_split_ja_at_punct():
    out = split_text("これは長い文です。次は別の文。", 8, JA)
    assert all(len(p) <= 8 for p in out)
    assert "".join(out) == "これは長い文です。次は別の文。"


def test_split_fallback_rule():
    # 未知语言通用规则：句末标点 + 空格断行
    out = split_text("First, second; third.", 12, _FALLBACK_RULE)
    assert all(len(p) <= 12 for p in out)


def test_inner_split_english_space():
    out = _inner_split("hello world this is a very long english sentence", 15, "")
    assert all(len(p) <= 15 for p in out)


def test_inner_split_zh_break_after():
    out = _inner_split("这是完全没有标点的超长句子内容没有任何逗号", 10, ZH["break_after"])
    assert all(len(p) <= 10 for p in out)


# ---------- split_by_ratio（副语言按比例同步拆） ----------

def test_split_by_ratio_single_part():
    assert split_by_ratio("hello world", ["短"]) == ["hello world"]


def test_split_by_ratio_even():
    assert split_by_ratio("hello world", ["一二", "三四"]) == ["hello", "world"]


def test_split_by_ratio_uneven():
    # 1:5 比例且英文无空格可吸附时，按字符比例切
    parts = split_by_ratio("helloworld", ["一", "二三四五六"])
    assert len(parts) == 2 and "".join(parts) == "helloworld"
    assert len(parts[0]) <= len(parts[1])  # 第一段更短


def test_split_by_ratio_uneven_zh_ja():
    # 日文原文按中文主句比例同步拆（语言无关场景）
    parts = split_by_ratio("これは長いです。", ["一二三四五", "六"])
    assert len(parts) == 2 and "".join(parts) == "これは長いです。"


# ---------- assign_time ----------

def test_assign_time_basic():
    times = assign_time(0.0, 10.0, ["aa", "aa"])
    assert times[0][0] == 0.0 and times[0][1] == 5.0
    assert times[1][0] == 5.0 and times[1][1] == 10.0


def test_assign_time_single():
    assert assign_time(1.0, 3.0, ["abc"]) == [(1.0, 3.0)]


def test_assign_time_uneven():
    times = assign_time(0.0, 12.0, ["a", "aa", "a"])
    # 1:2:1 -> 切点 3.0 / 9.0
    assert [round(t[1], 3) for t in times] == [3.0, 9.0, 12.0]
    assert times[0][0] == 0.0


# ---------- run 集成（语言无关：任意语言对） ----------


class _Cfg:
    """迷你 Config：只提供 refine.run 需要的语言解析与 refine 段。"""

    def __init__(self, src, tgt, primary=None):
        self._src, self._tgt = src, tgt
        self._primary = primary or tgt

    def translate_source_lang(self):
        return self._src

    def target_lang(self):
        return self._tgt

    def primary_lang(self):
        return self._primary

    def section(self, name):
        return {} if name == "refine" else {}


_LOG = logging.getLogger("test-refine")
logging.basicConfig(level=logging.CRITICAL)


def _write_segments(workdir: Path, lang: str, texts: list[str]) -> None:
    segs = [
        Segment(id=i, start=float(i) * 2, end=float(i) * 2 + 1.8, text=t)
        for i, t in enumerate(texts)
    ]
    save_segments(workdir / f"segments_{lang}.json", segs)


def test_run_ja_zh_pair(tmp_path):
    """日文源 -> 中文目标：refined.json 字段为 ja/zh（语言码），拆句按主语言（zh）规则。"""
    from quicksrt.steps.refine import run

    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "ja", ["これは長い日本語の文です。", "次は別の文。"])
    _write_segments(workdir, "zh", ["这是一条比较长的中文字幕句子。", "这是第二条。"])
    out = run(_Cfg("ja", "zh"), workdir, _LOG, force=True)

    items = json.loads(out.read_text(encoding="utf-8"))
    assert items, "refined.json 不应为空"
    first = items[0]
    assert "ja" in first and "zh" in first and "en" not in first
    # zh 为主语言（primary=target），ja 为副：比例同步拆分后条目数一致
    for it in items:
        assert it["zh"].strip() and it["ja"].strip()
    assert all(it["end"] >= it["start"] for it in items)


def test_run_en_zh_classic(tmp_path):
    """经典场景回归：英文源 -> 中文目标（默认链语义不变）。"""
    from quicksrt.steps.refine import run

    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "en", ["This is a fairly long English sentence.", "Second one."])
    _write_segments(workdir, "zh", ["这是一条比较长的英文字幕句子。", "第二条。"])
    out = run(_Cfg("en", "zh"), workdir, _LOG, force=True)
    items = json.loads(out.read_text(encoding="utf-8"))
    assert "zh" in items[0] and "en" in items[0]
    # zh 在上（primary）拆句，en 按比例同步
    assert len(items) >= 2


def test_run_primary_is_source(tmp_path):
    """主语言显式设为源语言：拆句按源语言规则（此处 en 按空格/句末标点拆）。"""
    from quicksrt.steps.refine import run

    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "en", ["This is a fairly long English sentence with multiple parts.", "Second."])
    _write_segments(workdir, "zh", ["这是一条比较长的英文句子。", "第二条。"])
    out = run(_Cfg("en", "zh", primary="en"), workdir, _LOG, force=True)
    items = json.loads(out.read_text(encoding="utf-8"))
    first = items[0]
    assert "en" in first and "zh" in first
    # en 主拆：长句应被拆开
    assert any("en" in it for it in items)


def test_run_same_lang_raises(tmp_path):
    """源语言与目标语言相同时报错。"""
    from quicksrt.steps.refine import run

    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "en", ["hello"])
    _write_segments(workdir, "en", ["hello"])
    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="源语言与目标语言相同"):
        run(_Cfg("en", "en"), workdir, _LOG, force=True)


def test_run_primary_not_in_pair_raises(tmp_path):
    """主语言既不是源也不是目标时报错。"""
    from quicksrt.steps.refine import run

    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "en", ["hello"])
    _write_segments(workdir, "zh", ["你好"])
    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="显示主语言"):
        run(_Cfg("en", "zh", primary="fr"), workdir, _LOG, force=True)
