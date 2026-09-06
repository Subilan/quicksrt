"""srt：v2 双语字幕格式（parse/render）、无损折行、基础稿生成与覆写守卫。"""

import json
import logging

import pytest

from quicksrt.models import save_segments, Segment
from quicksrt.steps.srt import (
    Cue,
    _clamp_and_detach,
    parse_cues,
    render_cues,
    run,
    wrap_lines,
)

_LOG = logging.getLogger("test-srt")
logging.basicConfig(level=logging.CRITICAL)


# ---------- 解析器 ----------

def test_parse_simple_bilingual():
    text = (
        "1\n00:00:00,000 --> 00:00:01,800\n你好\n--\nhello\n\n"
        "2\n00:00:02,000 --> 00:00:03,816\n你好啊\n--\nhello there\n"
    )
    cues, warnings = parse_cues(text)
    assert warnings == []
    assert len(cues) == 2
    assert cues[0].start == 0.0 and cues[0].end == 1.8
    assert cues[0].main_lines == ["你好"]
    assert cues[0].sub_lines == ["hello"]


def test_parse_crlf_bom_and_dot_ms(tmp_path):
    text = "1\r\n00:00:01.500 --> 00:00:02.25\r\nA\r\n--\r\nB\r\n"
    cues, _ = parse_cues(text)
    assert cues[0].start == 1.5 and cues[0].end == 2.25


def test_parse_no_separator_is_main_only():
    cues, _ = parse_cues("00:00:00,000 --> 00:00:01,000\n只有主语言\n")
    assert cues[0].sub_lines == []
    assert cues[0].main_lines == ["只有主语言"]


def test_parse_multiline_blocks():
    # 主/副块各自多行：角色由分隔行界定，行数无歧义
    text = "1\n00:00:00,000 --> 00:00:01,000\n主一\n主二\n--\n副一\n副二\n"
    cues, warnings = parse_cues(text)
    assert cues[0].main_lines == ["主一", "主二"]
    assert cues[0].sub_lines == ["副一", "副二"]


def test_parse_index_optional_and_tags_stripped():
    text = "00:00:00,000 --> 00:00:01,000\n<b>你好</b>\n<i>hi</i>\n"
    cues, _ = parse_cues(text)
    # 标签剥掉后两行均为主块（无分隔行）
    assert cues[0].main_lines == ["你好", "hi"]


def test_parse_structure_error_raises():
    with pytest.raises(ValueError, match="找不到时间行"):
        parse_cues("随便一段文字\n没有时间轴\n")


def test_parse_swapped_time_warns():
    cues, warnings = parse_cues("1\n00:00:05,000 --> 00:00:03,000\na\n--\nb\n")
    assert cues[0].start == 3.0 and cues[0].end == 5.0
    assert any("已交换" in w for w in warnings)


def test_parse_overlap_warns():
    text = "1\n00:00:00,000 --> 00:00:03,000\na\n--\nb\n\n2\n00:00:02,000 --> 00:00:04,000\nc\n--\nd\n"
    _, warnings = parse_cues(text)
    assert any("重叠" in w for w in warnings)


def test_parse_multiple_separators_warns():
    text = "1\n00:00:00,000 --> 00:00:01,000\na\n--\nb\n--\nc\n"
    cues, warnings = parse_cues(text)
    assert cues[0].main_lines == ["a"]
    assert cues[0].sub_lines == ["b", "--", "c"]
    assert any("分隔行" in w for w in warnings)


def test_parse_main_empty_promotes_sub():
    text = "1\n00:00:00,000 --> 00:00:01,000\n--\n副语言\n"
    cues, warnings = parse_cues(text)
    assert cues[0].main_lines == ["副语言"] and cues[0].sub_lines == []
    assert any("并入主语言" in w for w in warnings)


def test_parse_empty_text_skipped():
    cues, warnings = parse_cues("1\n00:00:00,000 --> 00:00:01,000\n\n2\n00:00:02,000 --> 00:00:03,000\nok\n")
    assert len(cues) == 1
    assert any("无文本" in w for w in warnings)


def test_custom_separator():
    text = "00:00:00,000 --> 00:00:01,000\n你好\n==\nhello\n"
    # 默认 -- 不识别 ==：整段并入主语言
    cues, _ = parse_cues(text)
    assert cues[0].sub_lines == []
    cues2, _ = parse_cues(text, separator="==")
    assert cues2[0].sub_lines == ["hello"]


def test_render_roundtrip():
    cues = [
        Cue(0.0, 1.234, ["主一", "主二"], ["副一"]),
        Cue(5.0, 6.0, ["只有主"]),
    ]
    text = render_cues(cues)
    back, warnings = parse_cues(text)
    assert warnings == []
    assert back == cues


def test_render_times_fmt():
    text = render_cues([Cue(1.0, 2.5, ["a"], ["b"])])
    assert "00:00:01,000 --> 00:00:02,500" in text


def test_file_separator_precedence():
    """解析现有文件的分隔符：refine/srt 记录优先，否则回退当前配置。"""
    from quicksrt.steps.srt import file_separator

    class C:
        def section(self, n):
            return {"srt": {"block_separator": "=="}}.get(n, {})

    assert file_separator(C(), {"srt": {"block_separator": "=="}}) == "=="
    assert file_separator(C(), {"refine": {"block_separator": ";;"}}) == ";;"  # 记录优先于配置
    assert file_separator(C(), {"srt": {"block_separator": "=="}, "refine": {"block_separator": ";;"}}) == ";;"
    assert file_separator(C(), {}) == "=="  # 无记录回退当前配置
    assert file_separator(type("C", (), {"section": lambda s, n: {}})(), {}) == "--"  # 默认


# ---------- 无损折行 ----------

def test_wrap_short_unchanged():
    assert wrap_lines("短文本", 42, 2) == ["短文本"]


def test_wrap_long_at_punct_lossless():
    text = "第一句。第二句。第三句。"
    lines = wrap_lines(text, 6, 2)
    assert "".join(lines) == text  # 无损：去掉换行还原原文
    assert len(lines) <= 2


def test_wrap_lossless_with_spaces():
    # 空格处折行不丢空格：去掉换行即可还原原文（refine 依赖）
    text = "hello world this is a long english line"
    lines = wrap_lines(text, 10, 2)
    assert "".join(lines) == text


def test_wrap_max_lines_respected():
    lines = wrap_lines("字" * 200, 10, 2)
    assert len(lines) == 2
    assert "".join(lines) == "字" * 200


def test_wrap_max_lines_one():
    lines = wrap_lines("hello world", 5, 1)
    assert lines == ["hello world"]


# ---------- 时间规整 ----------

def test_clamp_min_duration():
    out = _clamp_and_detach([(1.0, 1.2)], {"min_duration": 1.0})
    assert out == [(1.0, 2.0)]


def test_clamp_max_duration():
    out = _clamp_and_detach([(1.0, 5.0)], {"max_duration": 2.0})
    assert out == [(1.0, 3.0)]


def test_clamp_detach_overlap():
    out = _clamp_and_detach([(0.0, 1.0), (0.8, 2.0)], {})
    assert out[0][1] <= out[1][0]
    assert out[0][1] == 0.75  # max(start+0.1, next.start-0.05)


# ---------- 基础稿生成（run） ----------


class _Cfg:
    """迷你 Config：提供语言解析、[srt] 段与 output 相关接口。"""

    def __init__(self, primary="zh", secondary="en", srt=None, style_mode=None):
        self._primary, self._secondary = primary, secondary
        self._srt = {"max_line_chars": 42, "max_lines": 2, "min_duration": 1.0,
                     "max_duration": 0, "block_separator": "--"}
        if srt:
            self._srt.update(srt)
        self._style = {"mode": style_mode} if style_mode else {}

    def primary_lang(self):
        return self._primary

    def secondary_lang(self):
        return self._secondary

    def section(self, name):
        if name == "srt":
            return self._srt
        if name == "style":
            return self._style
        return {}


def _write_segments(workdir: "Path", lang: str, texts: list[str], times=None) -> None:
    segs = []
    for i, t in enumerate(texts):
        s, e = (times[i] if times else (float(i) * 2, float(i) * 2 + 1.8))
        segs.append(Segment(id=i, start=s, end=e, text=t))
    save_segments(workdir / f"segments_{lang}.json", segs)


def test_run_bilingual_pairing(tmp_path):
    from pathlib import Path

    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "zh", ["你好啊", "再见啦"])
    _write_segments(workdir, "en", ["hello there", "goodbye now"])
    out = run(_Cfg(), workdir, _LOG, force=True)

    cues, warnings = parse_cues(out.read_text(encoding="utf-8"))
    assert warnings == []
    assert [c.main_lines for c in cues] == [["你好啊"], ["再见啦"]]
    assert [c.sub_lines for c in cues] == [["hello there"], ["goodbye now"]]
    # 时间来自主语言（zh）segments
    assert cues[0].start == 0.0 and cues[0].end == 1.8


def test_run_single_lang_when_secondary_missing(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "zh", ["只有中文"])
    out = run(_Cfg(), workdir, _LOG, force=True)
    cues, _ = parse_cues(out.read_text(encoding="utf-8"))
    assert cues[0].main_lines == ["只有中文"] and cues[0].sub_lines == []


def test_run_primary_missing_falls_back(tmp_path, caplog):
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "en", ["english"])
    with caplog.at_level(logging.WARNING):
        out = run(_Cfg("zh", "en"), workdir, _LOG, force=True)
    cues, _ = parse_cues(out.read_text(encoding="utf-8"))
    assert cues[0].main_lines == ["english"]  # 回退到唯一可用语言
    assert "缺少 segments_zh.json" in caplog.text


def test_run_upgrade_to_bilingual_after_translate(tmp_path):
    """先只有主语言（未翻译）生成单语，翻译完成后重跑 srt 自动升级为双语。"""
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "zh", ["你好"])
    out = run(_Cfg(), workdir, _LOG, force=True)
    cues, _ = parse_cues(out.read_text(encoding="utf-8"))
    assert cues[0].sub_lines == []  # 先单语
    # 补上副语言 segments（模拟 translate 完成）
    _write_segments(workdir, "en", ["hello"])
    out = run(_Cfg(), workdir, _LOG)  # 无需 --force：resolved 语言纳入快照
    cues, _ = parse_cues(out.read_text(encoding="utf-8"))
    assert cues[0].sub_lines == ["hello"]


def test_run_no_segments_raises(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    with pytest.raises(FileNotFoundError):
        run(_Cfg(), workdir, _LOG, force=True)


def test_run_wraps_long_main(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "zh", ["字" * 100])
    _write_segments(workdir, "en", ["x"])
    out = run(_Cfg(srt={"max_line_chars": 10, "max_lines": 2}), workdir, _LOG, force=True)
    cues, _ = parse_cues(out.read_text(encoding="utf-8"))
    assert "".join(cues[0].main_lines) == "字" * 100  # 无损折行
    assert len(cues[0].main_lines) == 2


def _load_meta(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_run_guard_external_edit(tmp_path, caplog):
    """srt 之后文件被外部修改：重新 srt 默认跳过；--force 覆盖（先备份）。"""
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "zh", ["你好"])
    _write_segments(workdir, "en", ["hello"])
    out = run(_Cfg(), workdir, _LOG, force=True)
    meta_path = workdir / "meta.json"

    out.write_text("人工改过的内容\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        run(_Cfg(), workdir, _LOG)
    assert any("外部修改" in r.message for r in caplog.records)
    assert out.read_text(encoding="utf-8") == "人工改过的内容\n"  # 未被覆盖

    run(_Cfg(), workdir, _LOG, force=True)
    cues, _ = parse_cues(out.read_text(encoding="utf-8"))
    assert cues[0].main_lines == ["你好"]
    assert list(workdir.glob("subs.srt.bak.pre-srt-*")), "覆盖前应有备份"
    assert _load_meta(meta_path)["subs"]["owner"] == "srt"


def test_run_guard_owner_refine(tmp_path, caplog):
    """subs.srt 已被 refine 生成：再次 srt 默认跳过（不静默回滚 refine 结果）。"""
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "zh", ["你好"])
    _write_segments(workdir, "en", ["hello"])
    run(_Cfg(), workdir, _LOG, force=True)
    meta_path = workdir / "meta.json"
    m = _load_meta(meta_path)
    m["subs"] = {"owner": "refine", "digest": m["subs"]["digest"]}
    (meta_path).write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")

    with caplog.at_level(logging.INFO):
        run(_Cfg(), workdir, _LOG)
    assert any("由 refine 生成" in r.message for r in caplog.records)


def test_run_cfg_change_triggers_regenerate(tmp_path):
    """srt 参数变化（如 max_line_chars）：同一 owner=srt 且文件未被改时自动重建。"""
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_segments(workdir, "zh", ["字" * 60])
    _write_segments(workdir, "en", ["x"])
    run(_Cfg(srt={"max_line_chars": 42}), workdir, _LOG, force=True)
    out = run(_Cfg(srt={"max_line_chars": 10, "max_lines": 2}), workdir, _LOG)
    cues, _ = parse_cues(out.read_text(encoding="utf-8"))
    assert len(cues[0].main_lines) > 1  # 换更小 max_line_chars 后折行生效
