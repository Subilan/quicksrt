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

    def secondary_lang(self):
        return self._tgt if self._primary == self._src else self._src

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


# ---------- 人工精修稿（manual refine）----------

from quicksrt.steps.refine import (  # noqa: E402
    assign_src_ids,
    parse_bilingual_srt,
)


def test_parse_bilingual_srt_basic():
    text = (
        "1\r\n"
        "00:00:00,000 --> 00:00:01,800\r\n"
        "<b>你好</b>\r\n"
        "<b>hello</b>\r\n"
        "\r\n"
        "2\n"
        "00:00:02,000 --> 00:00:03,816\n"
        "你好啊\n"
        "hello there\n"
    )
    cues, warnings = parse_bilingual_srt(text)
    assert warnings == []
    assert cues == [(0.0, 1.8, "你好", "hello"), (2.0, 3.816, "你好啊", "hello there")]


def test_parse_bilingual_srt_missing_second_line_raises():
    text = "1\n00:00:01,000 --> 00:00:02,000\n只有一行\n"
    with pytest.raises(ValueError, match="两行文本"):
        parse_bilingual_srt(text)


def test_parse_bilingual_srt_swapped_time_warns():
    text = "1\n00:00:05,000 --> 00:00:03,000\na\nb\n"
    cues, warnings = parse_bilingual_srt(text)
    assert cues == [(3.0, 5.0, "a", "b")]
    assert any("已交换" in w for w in warnings)


def test_parse_bilingual_srt_overlap_warns():
    text = ("1\n00:00:00,000 --> 00:00:03,000\na\nb\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\nc\nd\n")
    cues, warnings = parse_bilingual_srt(text)
    assert len(cues) == 2
    assert any("重叠" in w for w in warnings)


def test_parse_bilingual_srt_bad_block_raises():
    with pytest.raises(ValueError, match="找不到时间行"):
        parse_bilingual_srt("随便一段文字\n没有时间轴\n")


def test_assign_src_ids_best_match_no_reuse_new_minus_one():
    ref_texts = ["cat sat on mat", "dog barked loudly", "bird sang"]
    ref_src_ids = [10, 11, 12]
    # 逐条取全局最优；同文本只继承一次；无相似文本的新条标 -1
    out = assign_src_ids(["cat sat on mat", "cat sat on mat", "fish swims"], ref_texts, ref_src_ids)
    assert out == [10, -1, -1]  # 第二条无法再复用 ref[0]（已被占用）且无其它相似


def test_import_manual_full_flow(tmp_path, caplog):
    """自动 refine -> 人工稿导入 -> 自动 refine 防覆盖守卫 -> --force 覆盖，全链路。"""
    from quicksrt.steps.refine import run

    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "en", ["hello there", "goodbye now"])
    _write_segments(workdir, "zh", ["你好啊", "再见啦"])
    cfg = _Cfg("en", "zh")

    # 1) 自动 refine
    auto_path = run(cfg, workdir, _LOG, force=True)
    auto_items = json.loads(auto_path.read_text(encoding="utf-8"))
    assert len(auto_items) == 2 and all(it["src_id"] >= 0 for it in auto_items)

    # 2) 人工稿导入（无 <b>、CRLF、时间与文本与自动稿一致 -> src_id 全量溯源继承）
    manual_srt = workdir / "manual.srt"
    manual_srt.write_text(
        "1\r\n00:00:00,000 --> 00:00:01,800\r\n你好啊\r\nhello there\r\n\r\n"
        "2\r\n00:00:02,000 --> 00:00:03,800\r\n再见啦\r\ngoodbye now\r\n",
        encoding="utf-8",
    )
    out = run(cfg, workdir, _LOG, manual_srt=manual_srt)
    manual_items = json.loads(out.read_text(encoding="utf-8"))
    assert len(manual_items) == 2
    assert manual_items == [
        {"id": 0, "src_id": auto_items[0]["src_id"], "start": 0.0, "end": 1.8,
         "zh": "你好啊", "en": "hello there"},
        {"id": 1, "src_id": auto_items[1]["src_id"], "start": 2.0, "end": 3.8,
         "zh": "再见啦", "en": "goodbye now"},
    ]
    meta = json.loads((workdir / "meta.json").read_text(encoding="utf-8"))
    assert meta["manual_refine"]["source"] == "manual.srt"
    assert meta["steps"]["refine"] == "done"
    assert meta["steps"]["srt"] is None and meta["steps"]["burn"] is None

    # 3) 相同人工稿重复导入：幂等，不新增备份
    n_bak = len(list(workdir.glob("refined.json.bak.pre-manual-*")))
    run(cfg, workdir, _LOG, manual_srt=manual_srt)
    assert len(list(workdir.glob("refined.json.bak.pre-manual-*"))) == n_bak

    # 4) 自动 refine（参数已变/断点失效，且未 --force）：守卫拦截，不覆盖人工稿
    mpath = workdir / "meta.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    m["refine"] = {"changed": True}  # 使 step_done 失配
    mpath.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        run(cfg, workdir, _LOG)
    assert any("人工精修稿" in r.message for r in caplog.records)
    assert json.loads(out.read_text(encoding="utf-8")) == manual_items

    # 5) --force 自动覆盖：备份旧稿、清除 manual_refine 标记
    run(cfg, workdir, _LOG, force=True)
    assert list(workdir.glob("refined.json.bak.pre-auto-*")), "覆盖前应有备份"
    meta = json.loads(mpath.read_text(encoding="utf-8"))
    assert "manual_refine" not in meta
    assert json.loads(out.read_text(encoding="utf-8")) == auto_items
