"""preview：分辨率解析、条目选取、ASS 生成与 ffmpeg 命令（run 的 ffmpeg 调用 mock）。"""

import base64
import json
import logging

import pytest

from quicksrt.steps import preview
from quicksrt.steps.preview import (
    EXAMPLES,
    RESOLUTIONS,
    _parse_bbox,
    inline_image_escape,
    pick_item,
    resolve_presets,
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
                return {"mode": "bilingual", "primary_lang": "zh", "zh_font_name": "F"}
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


# ---------- crop ----------

def test_parse_bbox():
    stderr = (
        "ffmpeg version ...\n"
        "[Parsed_bbox_3 @ 0x7f] n:0 pts:0 pts_time:0 x1:10 x2:500 y1:20 y2:100 w:491 h:81 "
        "crop=491:81:10:20 drawbox=0:922:1920:118\n"
    )
    assert _parse_bbox(stderr) == (491, 81, 10, 20)


def test_parse_bbox_none():
    assert _parse_bbox("no bbox output") is None


def test_run_crop(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "refined.json").write_text(json.dumps(_ITEMS, ensure_ascii=False), encoding="utf-8")
    (workdir / "meta.json").write_text(json.dumps({"title": "My Video"}), encoding="utf-8")

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "bilingual", "primary_lang": "zh", "zh_font_name": "F"}
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
    out = preview.run(FakeCfg(), workdir, _LOG, crop=True)

    assert out.name == "My_Video_preview_crop.png"
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
    assert not (tmp_path / "dist" / "My_Video_preview_crop_raw.png").exists()
    # 固定画布 ASS
    ass = (workdir / "preview_crop.ass").read_text(encoding="utf-8")
    assert "PlayResX: 3840" in ass and "PlayResY: 2160" in ass
    assert "第一句" in ass and "第二句" not in ass  # 只渲染选中条目（默认第 1 条）


def test_run_crop_with_background(tmp_path, monkeypatch):
    """crop 指定 --background：用它作截取背景色，裁剪保留纯色背景（不抠透明）。"""
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "refined.json").write_text(json.dumps(_ITEMS, ensure_ascii=False), encoding="utf-8")
    (workdir / "meta.json").write_text(json.dumps({}), encoding="utf-8")

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "bilingual", "primary_lang": "zh", "zh_font_name": "F"}
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
    preview.run(FakeCfg(), workdir, _LOG, crop=True, background="#202020")

    # 渲染背景用指定色（而非 [preview] background 默认值）
    assert "color=c=#202020:s=3840x2160:d=1" in calls[0]
    # 探测仍按背景色抠图找包围盒
    assert any("colorkey" in a for a in calls[1])
    # 裁剪保留背景：无 colorkey 抠透明
    assert any("crop=491:81:10:20" in a for a in calls[2])
    assert not any("colorkey" in a and "crop=491" in a for a in calls[2])


def test_run_crop_with_index(tmp_path, monkeypatch):
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
    out = preview.run(FakeCfg(), workdir, _LOG, crop=True, index=2)
    assert out.name == "work_preview_crop.png"  # 无标题时用 workdir 名
    ass = (workdir / "preview_crop.ass").read_text(encoding="utf-8")
    assert "second" in ass and "first" not in ass  # mono+en：只渲染第 2 条英文


# ---------- example ----------

def test_examples_content():
    """内置示例文本：三个枚举值，中英对照齐全；lorem 无中文对应，用滚滚长江东逝水。"""
    assert set(EXAMPLES) == {"lorem", "glass", "fox"}
    for text in EXAMPLES.values():
        assert text["zh"] and text["en"]
    assert EXAMPLES["lorem"]["zh"].startswith("滚滚长江东逝水")
    # 上阕 30 字，实测渲染行宽（1392px @1080p）与 lorem ipsum 英文行（1316px）相当
    assert 30 <= len(EXAMPLES["lorem"]["zh"]) <= 40
    assert "Lorem ipsum" in EXAMPLES["lorem"]["en"]
    assert EXAMPLES["glass"]["zh"] == "我能吞下玻璃而不伤身体"
    assert "eat glass" in EXAMPLES["glass"]["en"]
    assert "fox" in EXAMPLES["fox"]["en"] and "lazy dog" in EXAMPLES["fox"]["en"]


def test_run_example(tmp_path, monkeypatch):
    """--example lorem：用内置示例文本预览，不依赖 work 数据；auto 回退 1080p；ASS 临时文件清理。"""
    calls = []
    monkeypatch.setattr(preview.util, "run_cmd", lambda cmd, log, timeout=None: calls.append(cmd))

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "bilingual", "primary_lang": "zh", "zh_font_name": "F"}
            return {"background": "black"}

        def style_config(self, preset=None):
            return self.section("style")

    out = preview.run(FakeCfg(), None, _LOG, example="lorem")

    assert out.name == "example-lorem_preview_1080p.png"
    assert out.parent == tmp_path / "dist"
    assert "color=c=black:s=1920x1080:d=1" in calls[0]
    assert any("preview.ass" in a for a in calls[0])  # ASS 写在 output_dir（无 workdir）
    assert not (tmp_path / "dist" / "preview.ass").exists()  # 示例模式 ASS 已清理


def test_run_example_with_res(tmp_path, monkeypatch):
    """--example 指定 --res 时按指定分辨率渲染。"""
    calls = []
    monkeypatch.setattr(preview.util, "run_cmd", lambda cmd, log, timeout=None: calls.append(cmd))

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            return {"background": "black"}

        def style_config(self, preset=None):
            return {"mode": "mono", "primary_lang": "en"}

    out = preview.run(FakeCfg(), None, _LOG, example="glass", res="720p")
    assert out.name == "example-glass_preview_720p.png"
    assert "color=c=black:s=1280x720:d=1" in calls[0]


def test_run_example_crop(tmp_path, monkeypatch):
    """--example 与 --crop 组合：裁剪示例文本，输出 example-<名>_preview_crop.png。"""
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

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "bilingual", "primary_lang": "zh", "zh_font_name": "F"}
            return {"background": "black"}

        def style_config(self, preset=None):
            return self.section("style")

    out = preview.run(FakeCfg(), None, _LOG, example="fox", crop=True)
    assert out.name == "example-fox_preview_crop.png"
    assert len(calls) == 3
    assert not (tmp_path / "dist" / "preview_crop.ass").exists()
    assert not (tmp_path / "dist" / "example-fox_preview_crop_raw.png").exists()


def test_run_example_preset(tmp_path, monkeypatch):
    """--example 下 --preset 仍传给 style_config。"""
    seen = {}
    monkeypatch.setattr(preview.util, "run_cmd", lambda cmd, log, timeout=None: None)

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            return {"background": "black"}

        def style_config(self, preset=None):
            seen["preset"] = preset
            return {"mode": "mono", "primary_lang": "zh"}

    preview.run(FakeCfg(), None, _LOG, example="lorem", preset="plex")
    assert seen["preset"] == "plex"


def test_run_unknown_example():
    with pytest.raises(RuntimeError, match="未知示例"):
        preview.run(object(), None, _LOG, example="bogus")


# ---------- resolve_presets ----------

class _PresetCfg:
    presets = {"default": {}, "plex": {}, "plex_yellow": {}, "dazhizuo": {}, "dianshiju": {}}


def test_resolve_presets_none():
    assert resolve_presets(_PresetCfg(), None) is None
    assert resolve_presets(_PresetCfg(), None, False) is None


def test_resolve_presets_single_and_multi():
    assert resolve_presets(_PresetCfg(), "plex") == ["plex"]
    assert resolve_presets(_PresetCfg(), "plex,plex_yellow") == ["plex", "plex_yellow"]
    assert resolve_presets(_PresetCfg(), " plex ,dazhizuo ") == ["plex", "dazhizuo"]


def test_resolve_presets_all():
    assert resolve_presets(_PresetCfg(), None, True) == [
        "dazhizuo", "default", "dianshiju", "plex", "plex_yellow",
    ]


def test_resolve_presets_missing():
    with pytest.raises(ValueError, match="样式预设不存在"):
        resolve_presets(_PresetCfg(), "nope")


def test_resolve_presets_empty_value():
    with pytest.raises(ValueError, match="值为空"):
        resolve_presets(_PresetCfg(), " , ")


def test_resolve_presets_conflict():
    with pytest.raises(ValueError, match="互斥"):
        resolve_presets(_PresetCfg(), "plex", True)


def test_resolve_presets_all_empty():
    with pytest.raises(ValueError, match="无任何样式预设"):
        resolve_presets(type("C", (), {"presets": {}})(), None, True)


# ---------- preset 输出文件名 ----------

def test_run_preset_in_output_name(tmp_path, monkeypatch):
    """多预设渲染时文件名带预设后缀，避免互相覆盖。"""
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "refined.json").write_text(json.dumps(_ITEMS, ensure_ascii=False), encoding="utf-8")
    (workdir / "meta.json").write_text(json.dumps({"title": "My Video"}), encoding="utf-8")

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "bilingual", "primary_lang": "zh", "zh_font_name": "F"}
            return {"background": "black"}

        def style_config(self, preset=None):
            return self.section("style")

    monkeypatch.setattr(preview.util, "run_cmd", lambda cmd, log, timeout=None: None)
    out = preview.run(FakeCfg(), workdir, _LOG, res="720p", preset="plex")
    assert out.name == "My_Video_preview_plex_720p.png"


def test_run_crop_preset_in_output_name(tmp_path, monkeypatch):
    """crop + preset：文件名带预设后缀。"""
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "refined.json").write_text(json.dumps(_ITEMS, ensure_ascii=False), encoding="utf-8")
    (workdir / "meta.json").write_text(json.dumps({"title": "My Video"}), encoding="utf-8")

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "bilingual", "primary_lang": "zh", "zh_font_name": "F"}
            return {"background": "black"}

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
    out = preview.run(FakeCfg(), workdir, _LOG, crop=True, preset="dazhizuo")
    assert out.name == "My_Video_preview_dazhizuo_crop.png"


def test_run_example_preset_in_output_name(tmp_path, monkeypatch):
    """--example + preset：文件名带预设后缀。"""
    monkeypatch.setattr(preview.util, "run_cmd", lambda cmd, log, timeout=None: None)

    class FakeCfg:
        output_dir = tmp_path / "dist"

        def section(self, name):
            if name == "style":
                return {"mode": "bilingual", "primary_lang": "zh", "zh_font_name": "F"}
            return {"background": "black"}

        def style_config(self, preset=None):
            return self.section("style")

    out = preview.run(FakeCfg(), None, _LOG, example="glass", preset="plex", res="1080p")
    assert out.name == "example-glass_preview_plex_1080p.png"
