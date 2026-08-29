"""translate：结构化输出解析、分批、上下文渲染、批次重试兜底（mock 掉 HTTP 层）。"""

import json
import logging

import pytest

from quicksrt.models import Segment
from quicksrt.steps.translate import (
    _batches,
    _build_messages,
    _CONTEXT_MAX_LEN,
    _estimate_max_tokens,
    _parse_batch,
    _render_context,
    _system_prompt,
    DEFAULT_PROMPT_TEMPLATE,
    lang_name,
    render_prompt_template,
    translate_batch,
)

log = logging.getLogger("test")
logging.basicConfig(level=logging.CRITICAL)


# ---------- _parse_batch ----------

def test_parse_valid():
    out = _parse_batch('{"translations": [{"id": 1, "text": "欢迎。"}, {"id": 2, "text": "你好。"}]}')
    assert out == [{"id": 1, "text": "欢迎。"}, {"id": 2, "text": "你好。"}]


@pytest.mark.parametrize(
    "content",
    [
        "not json{{{",
        '{"foo": 1}',                       # 缺 translations
        '{"translations": {}}',             # translations 非数组
        '{"translations": [{"id": "x"}]}',  # id 类型错
        '{"translations": [{"id": 1}]}',    # 缺 text
    ],
)
def test_parse_invalid(content):
    with pytest.raises(RuntimeError, match="不符合结构"):
        _parse_batch(content)


# ---------- _batches ----------

def _segs(texts):
    return [Segment(id=i, start=float(i), end=float(i + 1), text=t) for i, t in enumerate(texts)]


def test_batches_boundary():
    # 恰好等于 max_chars 应同批；再多 1 字符换批
    b = _batches(_segs(["a" * 10, "b" * 10, "c"]), max_chars=21)
    assert [len(x) for x in b] == [2, 1]
    assert b[0][0]["id"] == 0 and b[1][0]["id"] == 2


def test_batches_single_oversized():
    b = _batches(_segs(["a" * 100, "b"]), max_chars=10)
    assert [len(x) for x in b] == [1, 1]


def test_batches_empty():
    assert _batches([], max_chars=10) == []


# ---------- _estimate_max_tokens ----------

def test_estimate_tokens_bounds():
    assert _estimate_max_tokens([]) == 2048
    assert _estimate_max_tokens([{"text": "x" * 10}]) == 2048
    assert _estimate_max_tokens([{"text": "x" * 5000}]) == 8192  # 封顶


# ---------- _render_context ----------

def test_render_context_basic():
    assert _render_context("{title} / {url}", {"title": "T", "url": "U"}) == "T / U"


def test_render_context_missing_key_empty():
    assert _render_context("a={missing} b={title}", {"title": "T"}) == "a= b=T"


def test_render_context_truncate_long():
    long_str = "x" * 2000
    out = _render_context("{v}", {"v": long_str})
    assert len(out) == _CONTEXT_MAX_LEN + 1 and out.endswith("…")


def test_render_context_format_spec():
    # 数字保持原类型，支持格式说明符
    assert _render_context("{duration:.0f}s", {"duration": 12.6}) == "13s"
    assert _render_context("{n}", {"n": 5}) == "5"


def test_render_context_empty_template():
    assert _render_context("", {"a": 1}) == ""


# ---------- render_prompt_template / lang_name ----------

def test_lang_name_known():
    assert lang_name("en") == "English"
    assert lang_name("zh") == "简体中文"
    assert lang_name("JA") == "日本語"  # 大小写不敏感


def test_lang_name_unknown_fallback():
    assert lang_name("xx") == "xx"


def test_render_prompt_template_placeholders():
    out = render_prompt_template("{source_lang} -> {target_lang} ({source}/{target})", "en", "zh")
    assert out == "English -> 简体中文 (en/zh)"


def test_render_prompt_template_long_first():
    # {source} 是 {source_lang} 的前缀：替换必须先长后短，不能把 {source_lang} 拆坏
    out = render_prompt_template("{source_lang}: {source}", "en", "zh")
    assert out == "English: en"


def test_render_prompt_template_keeps_braces():
    # 模板中其他字面花括号原样保留（如 JSON 结构示例）
    out = render_prompt_template('输出 {"id": {source}}', "en", "zh")
    assert out == '输出 {"id": en}'


def test_default_prompt_contains_lang_names():
    out = render_prompt_template(DEFAULT_PROMPT_TEMPLATE, "en", "zh")
    assert "English字幕" in out and "简体中文" in out
    assert '{"id": 数字' in out  # JSON 结构样例保留


# ---------- _system_prompt / _build_messages ----------

def test_system_prompt_without_context():
    assert _system_prompt(DEFAULT_PROMPT_TEMPLATE, "") == DEFAULT_PROMPT_TEMPLATE


def test_system_prompt_with_context():
    p = _system_prompt(DEFAULT_PROMPT_TEMPLATE, "视频简介")
    assert p.startswith(DEFAULT_PROMPT_TEMPLATE)
    assert "<video_context>\n视频简介\n</video_context>" in p


def test_build_messages():
    msgs = _build_messages([{"id": 1, "text": "hi"}], "prompt", "ctx")
    assert msgs[0]["role"] == "system" and "prompt" in msgs[0]["content"] and "ctx" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert json.loads(msgs[1]["content"]) == [{"id": 1, "text": "hi"}]


# ---------- translate_batch（mock _call_deepseek） ----------

def _no_sleep(_):
    pass


def test_translate_batch_success(monkeypatch):
    monkeypatch.setattr("quicksrt.steps.translate._call_deepseek", lambda *a, **k: [{"id": 1, "text": "译1"}, {"id": 2, "text": "译2"}])
    out = translate_batch("k", {}, [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}], log, retries=2, prompt="p", context="")
    assert {o["id"] for o in out} == {1, 2}


def test_translate_batch_retry_then_success(monkeypatch):
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        if calls["n"] < 2:
            return [{"id": 1, "text": "译1"}]  # 缺 id=2，触发重试
        return [{"id": 1, "text": "译1"}, {"id": 2, "text": "译2"}]

    monkeypatch.setattr("quicksrt.steps.translate._call_deepseek", fake)
    monkeypatch.setattr("quicksrt.steps.translate.time.sleep", _no_sleep)
    out = translate_batch("k", {}, [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}], log, retries=3, prompt="p", context="")
    assert calls["n"] == 2 and len(out) == 2


def test_translate_batch_fallback_single(monkeypatch):
    """整批反复失败 -> 对缺失条目逐条翻译成功。"""
    def fake(api_key, cfg, batch, prompt, context, log_):
        if len(batch) == 1:
            return [{"id": batch[0]["id"], "text": "单条译" + str(batch[0]["id"])}]
        raise RuntimeError("整批失败")

    monkeypatch.setattr("quicksrt.steps.translate._call_deepseek", fake)
    monkeypatch.setattr("quicksrt.steps.translate.time.sleep", _no_sleep)
    out = translate_batch("k", {}, [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}], log, retries=2, prompt="p", context="")
    assert {o["id"]: o["text"] for o in out} == {1: "单条译1", 2: "单条译2"}


def test_translate_batch_fallback_keep_original(monkeypatch):
    """单条也失败 -> 保留原文兜底。"""
    def fake(*a, **k):
        raise RuntimeError("全挂")

    monkeypatch.setattr("quicksrt.steps.translate._call_deepseek", fake)
    monkeypatch.setattr("quicksrt.steps.translate.time.sleep", _no_sleep)
    out = translate_batch("k", {}, [{"id": 1, "text": "original"}], log, retries=2, prompt="p", context="")
    assert out == [{"id": 1, "text": "original"}]


def test_translate_batch_unrecoverable(monkeypatch):
    """兜底后仍不完整（单条返回空数组） -> 抛错。"""
    def fake(api_key, cfg, batch, prompt, context, log_):
        if len(batch) == 1:
            return []  # 单条"成功"但空结果，id 补不上
        raise RuntimeError("整批失败")

    monkeypatch.setattr("quicksrt.steps.translate._call_deepseek", fake)
    monkeypatch.setattr("quicksrt.steps.translate.time.sleep", _no_sleep)
    with pytest.raises(RuntimeError, match="批次最终结果仍不完整"):
        translate_batch("k", {}, [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}], log, retries=2, prompt="p", context="")
