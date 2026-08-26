"""util：时间戳格式化、meta 状态、workdir 查找、颜色解析。"""

import json
from types import SimpleNamespace

import pytest

from quicksrt.util import (
    _ColoredFormatter,
    _color_enabled,
    find_latest_workdir,
    fmt_ts,
    font_available,
    load_meta,
    parse_ass_color,
    save_meta,
    step_done,
)


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


# ---------- 颜色解析（CSS -> ASS &HAABBGGRR） ----------


@pytest.mark.parametrize(
    ("css", "ass"),
    [
        ("#FFFFFF", "&H00FFFFFF"),
        ("#ffffff", "&H00FFFFFF"),
        ("#000000", "&H00000000"),
        ("#FF0000", "&H000000FF"),          # 红：BBGGRR
        ("#2196F3", "&H00F39621"),          # 蓝（BGR 反序）
        ("#FFF", "&H00FFFFFF"),            # 3 位简写
        ("#f00", "&H000000FF"),
        ("#00000080", "&H80000000"),      # 末尾 AA 直接映射 ASS alpha（半透明黑）
        ("#000000FF", "&HFF000000"),      # AA=FF 全透明（与 ASS 原生一致）
        ("#FFFFFF00", "&H00FFFFFF"),      # AA=00 完全不透明
        ("rgb(255, 255, 255)", "&H00FFFFFF"),
        ("rgb(255,0,0)", "&H000000FF"),
        ("rgb(0, 255, 0)", "&H0000FF00"),
        ("rgb(33, 150, 243)", "&H00F39621"),
        ("rgba(0, 0, 0, 0.5)", "&H80000000"),
        ("rgba(255, 255, 255, 1)", "&H00FFFFFF"),
        ("rgba(255, 255, 255, 1.0)", "&H00FFFFFF"),
        ("rgba(0,0,0,0)", "&HFF000000"),
        ("rgba(0, 0, 0, 0.2)", "&HCC000000"),  # (1-0.2)*255=204=0xCC
        ("RGBA(0, 0, 0, 0.5)", "&H80000000"),  # 大小写不敏感
    ],
)
def test_parse_ass_color_css(css, ass):
    assert parse_ass_color(css) == ass


def test_parse_ass_color_legacy_asses_through():
    """旧 ASS 格式 &HAABBGGRR 原样保留（统一大写）。"""
    assert parse_ass_color("&H00FFFFFF") == "&H00FFFFFF"
    assert parse_ass_color("&h80ff0000") == "&H80FF0000"
    assert parse_ass_color("&HFFFFFF") == "&H00FFFFFF"  # 6 位简写：alpha 视为 00


def test_parse_ass_color_clamps():
    """越界值自动钳制（r/g/b 0-255，a 0.0-1.0）。"""
    assert parse_ass_color("rgb(300, -10, 128)") == "&H008000FF"
    assert parse_ass_color("rgba(0, 0, 0, 1.5)") == "&H00000000"
    assert parse_ass_color("rgba(0, 0, 0, -0.2)") == "&HFF000000"


def test_parse_ass_color_invalid():
    for bad in ("", "   ", "blue", "#12", "#12345", "rgb(1, 2)", "rgba(1,2,3)", "#GGG", "&HZZ"):
        with pytest.raises(ValueError):
            parse_ass_color(bad)
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


def test_font_available_real():
    assert font_available("Noto Sans CJK SC") is True      # 本机已安装
    assert font_available("definitely-not-a-font-xyz") is False
    # fontconfig 通用家族名不依赖具体安装，始终视为可用
    assert font_available("sans-serif") is True
    assert font_available("Sans-Serif") is True


def test_font_available_no_fclist(monkeypatch):
    import subprocess

    # fc-list 不可用（如无 fontconfig 的环境）时跳过校验，不误报
    def boom(*args, **kwargs):
        raise FileNotFoundError("fc-list")

    monkeypatch.setattr(subprocess, "run", boom)
    assert font_available("Noto Sans CJK SC") is True


def test_color_enabled_precedence(monkeypatch):
    import logging
    import sys

    class _TTY:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stderr", _TTY())
    monkeypatch.delenv("QUICKSRT_NO_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert _color_enabled(None) is True        # tty 时自动开
    assert _color_enabled(False) is False      # 显式参数优先
    assert _color_enabled(True) is True
    monkeypatch.setenv("QUICKSRT_NO_COLOR", "1")
    assert _color_enabled(None) is False       # 环境变量关闭
    monkeypatch.delenv("QUICKSRT_NO_COLOR")
    monkeypatch.setenv("NO_COLOR", "1")
    assert _color_enabled(None) is False       # 标准 NO_COLOR 同样生效
    assert _color_enabled(True) is True        # 但显式 True 仍可强制


def test_colored_formatter_restores_levelname(caplog):
    import logging

    fmt = _ColoredFormatter("%(levelname)s %(message)s")
    record = logging.LogRecord("quicksrt", logging.WARNING, __file__, 1, "boom", None, None)
    out = fmt.format(record)
    assert "\x1b[33m" in out and "\x1b[0m" in out
    assert record.levelname == "WARNING"       # 恢复，不污染文件日志
    record2 = logging.LogRecord("quicksrt", logging.INFO, __file__, 1, "hi", None, None)
    assert "\x1b[32m" in fmt.format(record2)
