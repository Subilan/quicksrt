"""util：时间戳格式化、meta 状态、workdir 查找。"""

import json
from types import SimpleNamespace

import pytest

from quicksrt.util import find_latest_workdir, fmt_ts, load_meta, save_meta, step_done


@pytest.mark.parametrize(
    ("seconds", "expect"),
    [
        (0.0, "00:00:00,000"),
        (1.234, "00:00:01,234"),
        (59.9996, "00:01:00,000"),  # 四舍五入进位
        (3599.999, "00:59:59,999"),
        (3600.0, "01:00:00,000"),
        (-5.0, "00:00:00,000"),     # 负值钳到 0
    ],
)
def test_fmt_ts(seconds, expect):
    assert fmt_ts(seconds) == expect


def test_step_done():
    meta = {"steps": {"a": "done"}, "key": 1}
    assert step_done(meta, "a")
    assert not step_done(meta, "b")
    assert not step_done(meta, "a", key=2)
    assert step_done(meta, "a", key=1)
    assert not step_done({}, "a")


def test_meta_roundtrip(tmp_path):
    assert load_meta(tmp_path) == {}
    save_meta(tmp_path, {"steps": {"x": "done"}, "n": 1})
    assert load_meta(tmp_path) == {"steps": {"x": "done"}, "n": 1}


def test_find_latest_workdir(tmp_path):
    cfg = SimpleNamespace(work_dir=tmp_path)
    assert find_latest_workdir(cfg) is None  # 空目录

    import os
    # 仅含 meta.json 的视频工作目录算候选
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "meta.json").write_text("{}", encoding="utf-8")
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "meta.json").write_text("{}", encoding="utf-8")
    os.utime(tmp_path / "old", (1, 1))
    os.utime(tmp_path / "new", (2, 2))
    assert find_latest_workdir(cfg).name == "new"

    # 杂目录（无 meta.json）不计入候选，即使 mtime 最新
    (tmp_path / "junk").mkdir()
    os.utime(tmp_path / "junk", (3, 3))
    assert find_latest_workdir(cfg).name == "new"

    # 全部都是杂目录时返回 None
    (tmp_path / "old" / "meta.json").unlink()
    (tmp_path / "new" / "meta.json").unlink()
    assert find_latest_workdir(cfg) is None


def test_save_meta_creates_dir(tmp_path):
    p = tmp_path / "a" / "b"
    save_meta(p, {"x": 1})
    assert json.loads((p / "meta.json").read_text(encoding="utf-8")) == {"x": 1}
