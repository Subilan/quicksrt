"""refine：标点清理、拆句、英文同步拆分、时间分配（纯函数）。"""

import pytest

from quicksrt.steps.refine import (
    _inner_split,
    assign_time,
    split_en,
    split_zh,
    strip_end_punct,
)


# ---------- strip_end_punct ----------

@pytest.mark.parametrize(
    ("text", "expect"),
    [
        ("你好。", "你好"),
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
def test_strip_end_punct(text, expect):
    assert strip_end_punct(text) == expect


# ---------- split_zh ----------

def test_split_zh_short():
    assert split_zh("短文本", 42) == ["短文本"]


def test_split_zh_at_punct():
    out = split_zh("第一句，第二句，第三句", 5)
    assert out == ["第一句，", "第二句，", "第三句"]


def test_split_zh_merge_short_pieces():
    # 贪心合并不超过 max_chars
    out = split_zh("一二，三四五，六七八九", 7)
    assert all(len(p) <= 7 for p in out)
    assert "".join(out) == "一二，三四五，六七八九"


def test_split_zh_inner_split_fallback():
    # 无标点的超长句走行内断行
    out = split_zh("这是完全没有标点的超长句子内容没有任何逗号", 10)
    assert all(len(p) <= 10 for p in out)
    assert "".join(out) == "这是完全没有标点的超长句子内容没有任何逗号"


def test_split_zh_space_not_split_by_default():
    # 默认空格不是分句分隔符：分句仅发生在标点处，空格保留在句中
    out = split_zh("第一句，第二句 第三句", 8)
    assert out == ["第一句，", "第二句 第三句"]


def test_split_zh_space_split():
    text = "第一句 第二句 第三句"
    out = split_zh(text, 4, split_on_space=True)
    assert out == ["第一句 ", "第二句 ", "第三句"]


def test_split_zh_space_split_fullwidth():
    # 全角空格同样作为分句分隔符
    out = split_zh("第一句\u3000第二句", 4, split_on_space=True)
    assert out == ["第一句\u3000", "第二句"]


def test_split_zh_space_split_short():
    # 未超过 max_chars 不拆，与分句标点行为一致
    out = split_zh("第一句 第二句", 42, split_on_space=True)
    assert out == ["第一句 第二句"]


def test_split_zh_space_merge():
    # 空格分段后按 max_chars 贪心合并
    out = split_zh("一二 三四 五六 七八", 7, split_on_space=True)
    assert all(len(p) <= 7 for p in out)
    assert "".join(out) == "一二 三四 五六 七八"


def test_split_zh_space_with_punct():
    # 空格与分句标点混合时同样按 max_chars 贪心合并
    out = split_zh("第一句，第二句 第三句", 8, split_on_space=True)
    assert out == ["第一句，第二句 ", "第三句"]


def test_inner_split_english_space():
    out = _inner_split("hello world this is a very long english sentence", 15)
    assert all(len(p) <= 15 for p in out)


# ---------- split_en ----------

def test_split_en_single_part():
    assert split_en("hello world", ["短"]) == ["hello world"]


def test_split_en_ratio():
    # 中文两段 1:1，英文切成两段
    assert split_en("hello world", ["一二", "三四"]) == ["hello", "world"]


def test_split_en_ratio_uneven():
    # 1:5 比例且英文无空格可吸附时，按字符比例切
    parts = split_en("helloworld", ["一", "二三四五六"])
    assert len(parts) == 2 and "".join(parts) == "helloworld"
    assert len(parts[0]) <= len(parts[1])  # 第一段更短


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
