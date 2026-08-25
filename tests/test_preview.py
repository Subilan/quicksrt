"""preview：分辨率解析、条目选取、ASS 生成与 ffmpeg 命令（run 的 ffmpeg 调用 mock）。"""

import base64
import json
import logging

import pytest

from quicksrt.steps import preview
from quicksrt.steps.preview import (
    RESOLUTIONS,
    _parse_bbox,
    inline_image_escape,
    pick_item,
    resolve_size,
)

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


# ---------- inline_image_escape ----------

def test_inline_image_escape_roundtrip(tmp_path):
    img = tmp_path / "t.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    esc = inline_image_escape(img)
    assert esc.startswith("\x1b]1337;File=inline=1;width=100%:")
    assert esc.endswith("\a")
    payload = esc.split(":", 1)[1].removesuffix("\a")
    assert base64.b64decode(payload) == img.read_bytes()


def test_inline_image_escape_custom_width(tmp_path):
    img = tmp_path / "t.png"
    img.write_bytes(b"abc")
    assert inline_image_escape(img, "60%").startswith("\x1b]1337;File=inline=1;width=60%:")


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

        def style_config(self, preset=None):
            return self.section("style")

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

        def style_config(self, preset=None):
            return self.section("style")

    calls = []

    def fake_run_cmd(cmd, log, timeout=None):
        calls.append(cmd)

    monkeypatch.setattr(preview.util, "run_cmd", fake_run_cmd)
    preview.run(FakeCfg(), workdir, _LOG, res="1080p", index=1)
    assert "color=c=#202020:s=1920x1080:d=1" in calls[0]

    ass = (workdir / "preview.ass").read_text(encoding="utf-8")
    assert "Style: Secondary" not in ass  # mono 无副样式


def test_run_preset_passed_to_style_config(tmp_path, monkeypatch):
    """run 把 --preset 传给 style_config（临时切换预设）。"""
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "refined.json").write_text(json.dumps(_ITEMS, ensure_ascii=False), encoding="utf-8")
    (workdir / "meta.json").write_text(json.dumps({}), encoding="utf-8")

    seen = {}

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            return {} if name == "style" else {"background": "black"}

        def style_config(self, preset=None):
            seen["preset"] = preset
            return {"mode": "mono", "primary_lang": "zh"}

    monkeypatch.setattr(preview.util, "run_cmd", lambda cmd, log, timeout=None: None)
    preview.run(FakeCfg(), workdir, _LOG, res="720p", preset="plex")
    assert seen["preset"] == "plex"


def test_run_background_override(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "refined.json").write_text(json.dumps(_ITEMS, ensure_ascii=False), encoding="utf-8")
    (workdir / "meta.json").write_text(json.dumps({}), encoding="utf-8")

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "mono", "primary_lang": "zh"}
            return {"background": "#202020"}  # config 值

        def style_config(self, preset=None):
            return self.section("style")

    calls = []
    monkeypatch.setattr(preview.util, "run_cmd", lambda cmd, log, timeout=None: calls.append(cmd))
    preview.run(FakeCfg(), workdir, _LOG, res="720p", background="white")
    assert "color=c=white:s=1280x720:d=1" in calls[0]  # CLI 参数优先于 config


def test_run_without_refined(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()

    class FakeCfg:
        def section(self, name):
            return {}

    with pytest.raises(FileNotFoundError, match="refined.json"):
        preview.run(FakeCfg(), workdir, _LOG, res="720p")


# ---------- text-only ----------

def test_parse_bbox():
    stderr = (
        "ffmpeg version ...\n"
        "[Parsed_bbox_3 @ 0x7f] n:0 pts:0 pts_time:0 x1:10 x2:500 y1:20 y2:100 w:491 h:81 "
        "crop=491:81:10:20 drawbox=0:922:1920:118\n"
    )
    assert _parse_bbox(stderr) == (491, 81, 10, 20)


def test_parse_bbox_none():
    assert _parse_bbox("no bbox output") is None


def test_run_text_only(tmp_path, monkeypatch):
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

        def style_config(self, preset=None):
            return self.section("style")

    calls = []

    def fake_run_cmd(cmd, log, timeout=None):
        calls.append(cmd)
        from types import SimpleNamespace

        if any("bbox" in a for a in cmd):
            return SimpleNamespace(
                stderr="[Parsed_bbox_3 @ 0x7f] n:0 pts:0 pts_time:0 x1:10 x2:500 y1:20 y2:100 w:491 h:81 crop=491:81:10:20 drawbox=0:922:1920:118\n"
            )
        return SimpleNamespace(stderr="")

    monkeypatch.setattr(preview.util, "run_cmd", fake_run_cmd)
    out = preview.run(FakeCfg(), workdir, _LOG, text_only=True)

    assert out.name == "My_Video_preview_text.png"
    assert out.parent == FakeCfg().output_dir
    assert len(calls) == 3
    # 1. 用 [preview] background 默认值（black）渲染
    assert "color=c=black:s=3840x2160:d=1" in calls[0]
    # 2. 背景色抠图 + bbox 包围盒探测
    assert any("colorkey" in a for a in calls[1])
    assert any("bbox" in a for a in calls[1])
    # 3. 按解析出的包围盒裁剪，保留纯色背景（不抠透明）
    assert any("crop=491:81:10:20" in a for a in calls[2])
    assert not any("colorkey" in a and "crop=491" in a for a in calls[2])
    # 临时 raw 图已清理
    assert not (tmp_path / "dist" / "My_Video_preview_text_raw.png").exists()
    # 固定画布 ASS
    ass = (workdir / "preview_text.ass").read_text(encoding="utf-8")
    assert "PlayResX: 3840" in ass and "PlayResY: 2160" in ass
    assert "第一句" in ass and "第二句" not in ass  # 只渲染选中条目（默认第 1 条）


def test_run_text_only_with_background(tmp_path, monkeypatch):
    """text-only 指定 --background：用它作截取背景色，裁剪保留纯色背景（不抠透明）。"""
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "refined.json").write_text(json.dumps(_ITEMS, ensure_ascii=False), encoding="utf-8")
    (workdir / "meta.json").write_text(json.dumps({}), encoding="utf-8")

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "bilingual", "primary_lang": "zh", "font_name": "F"}
            return {"background": "black"}

        def style_config(self, preset=None):
            return self.section("style")

    calls = []

    def fake_run_cmd(cmd, log, timeout=None):
        calls.append(cmd)
        from types import SimpleNamespace

        if any("bbox" in a for a in cmd):
            return SimpleNamespace(
                stderr="[Parsed_bbox_3 @ 0x7f] n:0 pts:0 pts_time:0 x1:10 x2:500 y1:20 y2:100 w:491 h:81 crop=491:81:10:20 drawbox=0:922:1920:118\n"
            )
        return SimpleNamespace(stderr="")

    monkeypatch.setattr(preview.util, "run_cmd", fake_run_cmd)
    preview.run(FakeCfg(), workdir, _LOG, text_only=True, background="#202020")

    # 渲染背景用指定色（而非 [preview] background 默认值）
    assert "color=c=#202020:s=3840x2160:d=1" in calls[0]
    # 探测仍按背景色抠图找包围盒
    assert any("colorkey" in a for a in calls[1])
    # 裁剪保留背景：无 colorkey 抠透明
    assert any("crop=491:81:10:20" in a for a in calls[2])
    assert not any("colorkey" in a and "crop=491" in a for a in calls[2])


def test_run_text_only_with_index(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "refined.json").write_text(json.dumps(_ITEMS, ensure_ascii=False), encoding="utf-8")
    (workdir / "meta.json").write_text(json.dumps({}), encoding="utf-8")

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "mono", "primary_lang": "en"}
            return {}

        def style_config(self, preset=None):
            return self.section("style")

    monkeypatch.setattr(
        preview.util, "run_cmd",
        lambda cmd, log, timeout=None: __import__("types").SimpleNamespace(
            stderr=(
                "[Parsed_bbox_3 @ 0x7f] n:0 pts:0 pts_time:0 x1:10 x2:500 y1:20 y2:100 w:491 h:81 crop=491:81:10:20 drawbox=0:922:1920:118\n"
                if any("bbox" in a for a in cmd)
                else ""
            )
        ),
    )
    out = preview.run(FakeCfg(), workdir, _LOG, text_only=True, index=2)
    assert out.name == "work_preview_text.png"  # 无标题时用 workdir 名
    ass = (workdir / "preview_text.ass").read_text(encoding="utf-8")
    assert "second" in ass and "first" not in ass  # mono+en：只渲染第 2 条英文
