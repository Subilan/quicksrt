"""transcribe：ASR 原始响应解析（ms->s、异常时间修正、容错）。"""

import json

import pytest

from quicksrt.steps.transcribe import parse_result


def _write(tmp_path, raw):
    p = tmp_path / "asr_raw.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def test_parse_basic(tmp_path):
    p = _write(
        tmp_path,
        {
            "transcripts": [
                {"sentences": [
                    {
                        "begin_time": 4180, "end_time": 8260, "text": "Hello world.",
                        "words": [{"text": "Hello ", "begin_time": 4180, "end_time": 5000}],
                    },
                ]},
                {"sentences": [{"begin_time": 9000, "end_time": 9999, "text": "Second."}]},
            ]
        },
    )
    segs = parse_result(p)
    assert len(segs) == 2
    assert segs[0].id == 0 and segs[0].start == 4.18 and segs[0].end == 8.26
    assert segs[0].words[0].start == 4.18 and segs[0].words[0].end == 5.0
    assert segs[1].id == 1 and segs[1].text == "Second." and segs[1].words == []


def test_parse_fix_reversed_time(tmp_path):
    """end <= start 的异常片段拉长到 0.1s。"""
    p = _write(tmp_path, {"transcripts": [{"sentences": [{"begin_time": 1000, "end_time": 1000, "text": "x"}]}]})
    segs = parse_result(p)
    assert segs[0].end == segs[0].start + 0.1


def test_parse_tolerances(tmp_path):
    """words 为 null、缺 text 均可解析。"""
    p = _write(
        tmp_path,
        {"transcripts": [{"sentences": [
            {"begin_time": 1000, "end_time": 2000, "text": "x", "words": None},
            {"begin_time": 2000, "end_time": 3000},  # 缺 text
        ]}]},
    )
    segs = parse_result(p)
    assert segs[0].words == []
    assert segs[1].text == ""


def test_parse_missing_sentences(tmp_path):
    p = _write(tmp_path, {"transcripts": []})
    with pytest.raises(RuntimeError, match="无 sentences"):
        parse_result(p)


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",                                    # 非 JSON
        {"transcripts": [{"sentences": [{"end_time": 1}]}]},   # 缺 begin_time
        {"transcripts": [{"sentences": [{"begin_time": "x", "end_time": 1}]}]},  # 类型错
    ],
)
def test_parse_invalid(tmp_path, raw):
    p = tmp_path / "asr_raw.json"
    p.write_text(json.dumps(raw) if not isinstance(raw, str) else raw, encoding="utf-8")
    with pytest.raises(RuntimeError, match="ASR 结果解析失败"):
        parse_result(p)
