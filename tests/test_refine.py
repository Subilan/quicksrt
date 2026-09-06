"""refine：标点清理、拆句、副语言按比例同步拆分、时间分配（纯函数）+ v2 就地改写集成。"""

import json
import logging

import pytest

from quicksrt.steps.refine import (
    _inner_split,
    _FALLBACK_RULE,
    _flatten,
    assign_time,
    lang_rule,
    split_by_ratio,
    split_text,
    strip_end_punct,
)
from quicksrt.steps.srt import Cue, parse_cues, render_cues
from quicksrt.util import file_digest, save_meta

ZH = lang_rule("zh")
EN = lang_rule("en")
JA = lang_rule("ja")

_LOG = logging.getLogger("test-refine")
logging.basicConfig(level=logging.CRITICAL)


# ---------- lang_rule ----------

def test_lang_rule_builtin():
    assert ZH["split_punct"] == "，、；"
    assert EN["space_separator"] is True
    assert JA["break_after"]


def test_lang_rule_unknown_fallback():
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
    assert strip_end_punct("Hello.", EN["strip_punct"]) == "Hello."
    assert strip_end_punct("Hello world!", EN["strip_punct"]) == "Hello world!"


# ---------- split_text（中文规则） ----------

def test_split_zh_short():
    assert split_text("短文本", 42, ZH) == ["短文本"]


def test_split_zh_at_punct():
    out = split_text("第一句，第二句，第三句", 5, ZH)
    assert out == ["第一句，", "第二句，", "第三句"]


def test_split_zh_merge_short_pieces():
    out = split_text("一二，三四五，六七八九", 7, ZH)
    assert all(len(p) <= 7 for p in out)
    assert "".join(out) == "一二，三四五，六七八九"


def test_split_zh_inner_split_fallback():
    out = split_text("这是完全没有标点的超长句子内容没有任何逗号", 10, ZH)
    assert all(len(p) <= 10 for p in out)
    assert "".join(out) == "这是完全没有标点的超长句子内容没有任何逗号"


def test_split_zh_space_not_split_by_default():
    out = split_text("第一句，第二句 第三句", 8, ZH)
    assert out == ["第一句，", "第二句 第三句"]


def test_split_zh_space_split_override():
    text = "第一句 第二句 第三句"
    out = split_text(text, 4, ZH, split_on_space=True)
    assert out == ["第一句 ", "第二句 ", "第三句"]


def test_split_zh_space_split_fullwidth():
    out = split_text("第一句\u3000第二句", 4, ZH, split_on_space=True)
    assert out == ["第一句\u3000", "第二句"]


def test_split_zh_space_merge():
    out = split_text("一二 三四 五六 七八", 7, ZH, split_on_space=True)
    assert all(len(p) <= 7 for p in out)
    assert "".join(out) == "一二 三四 五六 七八"


# ---------- split_text（英文/日文/通用规则） ----------

def test_split_en_at_punct():
    out = split_text("First sentence. Second sentence. Third.", 20, EN)
    assert out == ["First sentence. ", "Second sentence. ", "Third."]


def test_split_en_by_space():
    out = split_text("hello world this is a long text", 14, EN)
    assert all(len(p) <= 14 for p in out)
    assert "".join(out) == "hello world this is a long text"


def test_split_ja_at_punct():
    out = split_text("これは長い文です。次は別の文。", 8, JA)
    assert all(len(p) <= 8 for p in out)
    assert "".join(out) == "これは長い文です。次は別の文。"


def test_split_fallback_rule():
    out = split_text("First, second; third.", 12, _FALLBACK_RULE)
    assert all(len(p) <= 12 for p in out)


def test_inner_split_english_space():
    out = _inner_split("hello world this is a very long english sentence", 15, "")
    assert all(len(p) <= 15 for p in out)


# ---------- split_by_ratio / assign_time / _flatten ----------

def test_split_by_ratio_single_part():
    assert split_by_ratio("hello world", ["短"]) == ["hello world"]


def test_split_by_ratio_even():
    assert split_by_ratio("hello world", ["一二", "三四"]) == ["hello", "world"]


def test_assign_time_basic():
    times = assign_time(0.0, 10.0, ["aa", "aa"])
    assert times[0][0] == 0.0 and times[0][1] == 5.0
    assert times[1][0] == 5.0 and times[1][1] == 10.0


def test_flatten_removes_wrap_newlines():
    # 无损折行的行去掉换行即原文
    assert _flatten(["主一", "主二"]) == "主一主二"


# ---------- 集成：v2 文件就地改写 ----------


class _Cfg:
    def __init__(self, primary="zh", secondary="en", refine=None, srt=None):
        self._p, self._s = primary, secondary
        self._refine = {
            "max_chars": 42, "min_gap": 0.35, "strip_end_punct": True,
            "split_on_space": False,
        }
        if refine:
            self._refine.update(refine)
        self._srt = {"block_separator": "--"}
        if srt:
            self._srt.update(srt)

    def primary_lang(self):
        return self._p

    def secondary_lang(self):
        return self._s

    def section(self, name):
        return self._refine if name == "refine" else self._srt


def _write_base(workdir, text, owner="srt", digest=None, meta_extra=None):
    """落盘 subs.srt + 记录 owner/digest（模拟 srt 环节产物）。"""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "subs.srt").write_text(text, encoding="utf-8")
    meta = {"subs": {
        "owner": owner,
        "digest": digest if digest is not None else file_digest(workdir / "subs.srt"),
    }}
    if meta_extra:
        meta.update(meta_extra)
    save_meta(workdir, meta)


def _runcfg(workdir, cfg):
    from quicksrt.steps.refine import run
    return run(cfg, workdir, _LOG)


def test_run_zh_primary_split(tmp_path):
    """主语言拆句 + 副语言比例同步：双语条目一一对应，各段单行。"""
    workdir = tmp_path / "work"
    base = render_cues([
        Cue(0.0, 10.0, ["所以总结一下，地图中的继电器向控制台发送命令，控制台在玩家实体中启动倒计时，然后发送到神经毒素屏幕"], ["So in summary, the relay sends the command to the console, which starts the countdown."]),
        Cue(12.0, 14.0, ["短句"], ["short."]),
    ])
    _write_base(workdir, base)
    out = _runcfg(workdir, _Cfg(refine={"max_chars": 20}))
    cues, warnings = parse_cues(out.read_text(encoding="utf-8"))
    assert not warnings
    assert len(cues) >= 3
    for c in cues:
        assert len(c.main_lines) == 1
        if c.sub_lines:
            assert len(c.sub_lines) == 1
        assert c.end >= c.start
    # 拆句后所有 main 拼起来 == 原文去掉分句末尾标点（各 chunk 末尾的逗号/句号被 strip）
    pieces = "".join(c.main_lines[0] for c in cues)
    raw = "所以总结一下，地图中的继电器向控制台发送命令，控制台在玩家实体中启动倒计时，然后发送到神经毒素屏幕"
    assert pieces == raw.replace("，", "") + "短句"


def test_run_strips_end_punct(tmp_path):
    workdir = tmp_path / "work"
    base = render_cues([Cue(0.0, 5.0, ["你好啊。"], ["hello."])])
    _write_base(workdir, base)
    out = _runcfg(workdir, _Cfg())
    cues, _ = parse_cues(out.read_text(encoding="utf-8"))
    assert cues[0].main_lines == ["你好啊"]
    assert cues[0].sub_lines == ["hello."]  # en 规则不去句号


def test_run_keeps_main_only_cue(tmp_path):
    """无副块的 cue（单语内容）：refine 只拆主语言。"""
    workdir = tmp_path / "work"
    base = render_cues([Cue(0.0, 10.0, ["只有主语言的长句子，需要拆分"], [])])
    _write_base(workdir, base)
    out = _runcfg(workdir, _Cfg(refine={"max_chars": 8}))
    cues, _ = parse_cues(out.read_text(encoding="utf-8"))
    assert len(cues) >= 2
    assert all(c.sub_lines == [] for c in cues)


def test_run_seam_fill(tmp_path):
    """微小间隔/重叠被填平。"""
    workdir = tmp_path / "work"
    base = render_cues([
        Cue(0.0, 1.0, ["甲"], ["a"]),
        Cue(1.05, 2.0, ["乙"], ["b"]),   # 间隔 0.05 < min_gap 0.35
        Cue(2.5, 3.0, ["丙"], ["c"]),    # 间隔 0.5 > min_gap：保留
    ])
    _write_base(workdir, base)
    out = _runcfg(workdir, _Cfg())
    cues, _ = parse_cues(out.read_text(encoding="utf-8"))
    assert cues[0].end == cues[1].start
    assert cues[1].end < cues[2].start


def test_run_skip_when_unchanged(tmp_path, caplog):
    workdir = tmp_path / "work"
    _write_base(workdir, render_cues([Cue(0.0, 1.0, ["你好"], ["hello"])]))
    _runcfg(workdir, _Cfg())
    before = (workdir / "subs.srt").read_text(encoding="utf-8")
    with caplog.at_level(logging.INFO):
        _runcfg(workdir, _Cfg())
    assert any("已完成，跳过" in r.message for r in caplog.records)
    assert (workdir / "subs.srt").read_text(encoding="utf-8") == before


def test_run_guard_external_edit(tmp_path, caplog):
    """refine 之后文件被人工修改：再次 refine 默认跳过；--force 覆盖并备份。"""
    from quicksrt.steps.refine import run

    workdir = tmp_path / "work"
    _write_base(workdir, render_cues([Cue(0.0, 1.0, ["你好"], ["hello"])]))
    out = workdir / "subs.srt"
    # 第一次 refine 后手工编辑（owner 已变 refine：守卫以 digest 为准）
    _runcfg(workdir, _Cfg())
    out.write_text(
        "00:00:00,000 --> 00:00:01,000\n人工改写\n--\nhuman edit\n", encoding="utf-8"
    )
    manual = out.read_text(encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        run(_Cfg(), workdir, _LOG)
    assert any("外部修改" in r.message for r in caplog.records)
    assert out.read_text(encoding="utf-8") == manual  # 未被覆盖

    run(_Cfg(), workdir, _LOG, force=True)
    assert list(workdir.glob("subs.srt.bak.pre-auto-*")), "覆盖前应有备份"
    meta = json.loads((workdir / "meta.json").read_text(encoding="utf-8"))
    assert meta["subs"]["owner"] == "refine"


def test_run_missing_subs_srt_raises(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    with pytest.raises(FileNotFoundError, match="subs.srt"):
        _runcfg(workdir, _Cfg())


def test_run_keeps_file_separator_after_config_change(tmp_path):
    """文件以旧分隔符生成后改了配置：refine 仍按文件记录的分隔符解析/书写，不误读不重写。"""
    from quicksrt.steps.refine import run

    workdir = tmp_path / "work"
    base = "00:00:00,000 --> 00:00:01,000\n你好。\n--\nhello.\n"
    _write_base(workdir, base, meta_extra={"srt": {"block_separator": "--"}})
    # 配置改为 ==，但文件是 -- 生成的：仍按 -- 解析（hello 仍是副语言）
    out = run(_Cfg(srt={"block_separator": "=="}), workdir, _LOG, force=True)
    text = out.read_text(encoding="utf-8")
    assert "\n--\n" in text  # 书写仍用文件分隔符，未重写
    cues, _ = parse_cues(text)
    assert cues[0].sub_lines == ["hello."]


def test_run_custom_separator():
    """分隔符可配置：按当前配置解析/书写。"""
    import tempfile
    from quicksrt.steps.refine import run
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td) / "work"
        text = "00:00:00,000 --> 00:00:01,000\n你好。\n==\nhello.\n"
        _write_base(workdir, text)
        out = run(_Cfg(srt={"block_separator": "=="}), workdir, _LOG, force=True)
        cues, _ = parse_cues(out.read_text(encoding="utf-8"), separator="==")
        assert cues[0].main_lines == ["你好"]
        assert cues[0].sub_lines == ["hello."]
