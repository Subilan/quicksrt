"""preview：分辨率解析、条目选取、ASS 生成与 ffmpeg 命令（run 的 ffmpeg 调用 mock）。"""

import json
import logging

import pytest

from quicksrt.steps import preview
from quicksrt.steps.preview import RESOLUTIONS, pick_item, resolve_size

_LOG = logging.getLogger("test_preview")

_ITEMS = [
    {"id": 0, "start": 1.0, "end": 2.0, "zh": "第一句", "en": "first"},
    {"id": 1, "start": 3.0, "end": 4.0, "zh": "第二句", "en": "second"},
]


# ---------- resolve_size ----------

def test_resolve_size_presets():
    assert resolve_size("720p", None) == (1280, 720, "720p")
    assert resolve_size("1080p", None) == (1920, 1080, "1080p")
    assert resolve_size("4K", None) == (3840, 2160, "4k")
    assert set(RESOLUTIONS) == {"720p", "1080p", "4k"}


def test_resolve_size_auto(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(preview.util, "probe_video", lambda _p: {"width": 1280, "height": 720})
    assert resolve_size("auto", tmp_path) == (1280, 720, "1280x720")


def test_resolve_size_auto_without_video(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_size("auto", tmp_path)


def test_resolve_size_invalid():
    with pytest.raises(RuntimeError, match="不支持的分辨率"):
        resolve_size("8k", None)


# ---------- pick_item ----------

def test_pick_item_normalizes_time():
    it = pick_item(_ITEMS, 2)
    assert it["zh"] == "第二句"
    assert it["start"] == 0.0 and it["end"] == 1.0  # 归一化到首帧


def test_pick_item_out_of_range():
    with pytest.raises(RuntimeError, match="超出范围"):
        pick_item(_ITEMS, 3)
    with pytest.raises(RuntimeError, match="超出范围"):
        pick_item(_ITEMS, 0)


# ---------- run ----------

def test_run_builds_ass_and_calls_ffmpeg(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "refined.json").write_text(json.dumps(_ITEMS, ensure_ascii=False), encoding="utf-8")
    (workdir / "meta.json").write_text(json.dumps({"title": "My Video"}), encoding="utf-8")

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "bilingual", "primary_lang": "zh", "font_name": "F"}
            return {"background": "black"}

    calls = []

    def fake_run_cmd(cmd, log, timeout=None):
        calls.append(cmd)

    monkeypatch.setattr(preview.util, "run_cmd", fake_run_cmd)
    out = preview.run(FakeCfg(), workdir, _LOG, res="720p", index=2)

    assert out.name == "My_Video_preview_720p.png"
    assert out.parent == FakeCfg().output_dir
    cmd = calls[0]
    assert "color=c=black:s=1280x720:d=1" in cmd
    assert "-frames:v" in cmd and cmd[cmd.index("-frames:v") + 1] == "1"

    ass = (workdir / "preview.ass").read_text(encoding="utf-8")
    assert "PlayResX: 1280" in ass and "PlayResY: 720" in ass
    assert "第二句" in ass and "first" not in ass  # 只渲染选中条目


def test_run_custom_background(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "refined.json").write_text(json.dumps(_ITEMS, ensure_ascii=False), encoding="utf-8")
    (workdir / "meta.json").write_text(json.dumps({}), encoding="utf-8")

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "mono", "primary_lang": "en"}
            return {"background": "#202020"}

    calls = []

    def fake_run_cmd(cmd, log, timeout=None):
        calls.append(cmd)

    monkeypatch.setattr(preview.util, "run_cmd", fake_run_cmd)
    preview.run(FakeCfg(), workdir, _LOG, res="1080p", index=1)
    assert "color=c=#202020:s=1920x1080:d=1" in calls[0]

    ass = (workdir / "preview.ass").read_text(encoding="utf-8")
    assert "Style: Secondary" not in ass  # mono 无副样式


def test_run_without_refined(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()

    class FakeCfg:
        def section(self, name):
            return {}

    with pytest.raises(FileNotFoundError, match="refined.json"):
        preview.run(FakeCfg(), workdir, _LOG, res="720p")
