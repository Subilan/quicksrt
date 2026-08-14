"""models：Segment/Word pydantic 模型与 load/save 序列化。"""

import json

import pytest

from quicksrt.models import Segment, Word, load_segments, save_segments


def seg(id=1, start=0.0, end=1.5, text="hello", words=None):
    return Segment(id=id, start=start, end=end, text=text, words=words or [])


def test_roundtrip_with_words(tmp_path):
    p = tmp_path / "segments.json"
    s = seg(words=[Word(text="hello ", start=0.0, end=0.5), Word(text="world", start=0.5, end=1.0)])
    save_segments(p, [s])
    loaded = load_segments(p)
    assert loaded == [s]
    assert loaded[0].words[1].text == "world"
    assert isinstance(loaded[0].start, float)


def test_roundtrip_empty_words(tmp_path):
    p = tmp_path / "segments.json"
    save_segments(p, [seg()])
    assert load_segments(p)[0].words == []


def test_disk_format(tmp_path):
    """磁盘 JSON 与旧版 asdict 输出一致（键序 id/start/end/text/words）。"""
    p = tmp_path / "segments.json"
    save_segments(p, [seg()])
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw == [{"id": 1, "start": 0.0, "end": 1.5, "text": "hello", "words": []}]


def test_missing_words_defaults():
    s = Segment.model_validate({"id": 1, "start": 0, "end": 1, "text": "t"})
    assert s.words == []
    assert isinstance(s.start, float)  # int 时间自动转 float


def test_bad_data_raises(tmp_path):
    p = tmp_path / "segments.json"
    p.write_text(json.dumps([{"id": "x", "start": 1, "end": 2, "text": "t"}]))
    with pytest.raises(RuntimeError, match="不符合 Segment 结构"):
        load_segments(p)


def test_bad_word_raises(tmp_path):
    p = tmp_path / "segments.json"
    p.write_text(json.dumps([{"id": 1, "start": 1, "end": 2, "text": "t", "words": [{"text": "w"}]}]))
    with pytest.raises(RuntimeError, match="不符合 Segment 结构"):
        load_segments(p)


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_segments(tmp_path / "nope.json")
