"""config：默认配置、toml 合并、.env 加载。"""

import os
from pathlib import Path

import pytest

from quicksrt.config import Config, _deep_merge, _load_dotenv, load_config


def test_deep_merge():
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    override = {"a": {"y": 3, "z": 4}, "b": 5, "c": 6}
    merged = _deep_merge(base, override)
    assert merged == {"a": {"x": 1, "y": 3, "z": 4}, "b": 5, "c": 6}
    assert base == merged  # 原地合并


def test_load_config_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # 避免读到项目真实 config.toml / .env
    cfg = load_config()
    assert cfg.section("translate")["model"] == "deepseek-chat"
    assert cfg.section("translate")["max_concurrency"] == 4
    assert cfg.work_dir == Path("work")


def test_load_config_merge(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        "[translate]\nmodel = \"my-model\"\ntemperature = 0.9\n\n[extra]\nx = 1\n",
        encoding="utf-8",
    )
    cfg = load_config()
    tr = cfg.section("translate")
    assert tr["model"] == "my-model"
    assert tr["temperature"] == 0.9
    assert tr["max_concurrency"] == 4  # 未覆盖的默认值保留
    assert cfg.section("extra") == {"x": 1}


def test_config_asr_endpoint(monkeypatch):
    cfg = Config({"asr": {"endpoint": "https://e/", "region": "cn-beijing"}}, None)
    assert cfg.asr_endpoint == "https://e"

    cfg2 = Config({"asr": {"endpoint": "", "region": "cn-beijing"}}, None)
    monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "ws123")
    assert cfg2.asr_endpoint == "https://ws123.cn-beijing.maas.aliyuncs.com/api/v1"

    cfg3 = Config({"asr": {"endpoint": "", "region": "cn-beijing"}}, None)
    monkeypatch.delenv("DASHSCOPE_WORKSPACE_ID")
    with pytest.raises(RuntimeError, match="未配置 ASR endpoint"):
        _ = cfg3.asr_endpoint


def test_load_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "# comment\n\nKEY_A=value1\nKEY_B = \"quoted\"\nKEY_C='single'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("KEY_A", raising=False)
    monkeypatch.delenv("KEY_B", raising=False)
    monkeypatch.delenv("KEY_C", raising=False)
    monkeypatch.setenv("KEY_A", "existing")  # setdefault 语义：不覆盖已存在
    _load_dotenv(tmp_path / ".env")
    assert os.environ["KEY_A"] == "existing"
    assert os.environ["KEY_B"] == "quoted"
    assert os.environ["KEY_C"] == "single"


# ---------- style_config（预设展开） ----------

_PRESETS = {
    "default": {
        "zh_font_name": "Noto Sans CJK SC", "font_size_ratio": 0.05,
        "mode": "bilingual", "primary_lang": "zh", "zh_bold": False,
    },
    "bold_en": {
        "zh_font_name": "Arial", "primary_lang": "en",
        "en_font_name": "Arial", "en_bold": True, "en_italic": True,
    },
}


def test_style_config_without_preset():
    cfg = Config({"style": {"font_size_ratio": 0.07}}, None)
    assert cfg.style_config() == {"font_size_ratio": 0.07}


def test_style_config_preset_expand():
    cfg = Config({"style": {"preset": "default"}}, None, _PRESETS)
    assert cfg.style_config()["zh_font_name"] == "Noto Sans CJK SC"
    assert cfg.style_config()["primary_lang"] == "zh"
    assert cfg.style_config()["preset"] == "default"


def test_style_config_preset_overridden_by_style():
    cfg = Config({"style": {"preset": "bold_en", "font_size_ratio": 0.09}}, None, _PRESETS)
    style = cfg.style_config()
    assert style["primary_lang"] == "en"          # 预设展开
    assert style["en_bold"] is True             # 预设展开
    assert style["en_italic"] is True           # 预设展开
    assert style["font_size_ratio"] == 0.09       # [style] 显式键覆盖


def test_style_config_missing_preset_raises():
    cfg = Config({"style": {"preset": "nope"}}, None, _PRESETS)
    with pytest.raises(RuntimeError, match="样式预设不存在: nope（可用: bold_en, default）"):
        cfg.style_config()


def test_style_config_cli_preset_overrides():
    """CLI --preset 临时覆盖 config.toml 的 preset，但 [style] 显式键仍覆盖 preset。"""
    cfg = Config({"style": {"preset": "default", "font_size_ratio": 0.09}}, None, _PRESETS)
    style = cfg.style_config(preset="bold_en")
    assert style["primary_lang"] == "en"          # 来自 --preset 的 bold_en
    assert style["en_bold"] is True               # 来自 --preset 的 bold_en
    assert style["font_size_ratio"] == 0.09        # [style] 显式键仍覆盖
    assert style["preset"] == "bold_en"


def test_style_config_cli_preset_without_style_preset():
    """config.toml 未写 preset 时，--preset 作为基底展开。"""
    cfg = Config({"style": {"font_size_ratio": 0.07}}, None, _PRESETS)
    style = cfg.style_config(preset="bold_en")
    assert style["primary_lang"] == "en"
    assert style["font_size_ratio"] == 0.07        # 显式键仍覆盖 preset 的默认值


def test_style_config_cli_preset_missing_raises():
    cfg = Config({"style": {"preset": "default"}}, None, _PRESETS)
    with pytest.raises(RuntimeError, match="样式预设不存在: nope（可用: bold_en, default）"):
        cfg.style_config(preset="nope")


# ---------- presets.toml 预设间继承 ----------

_INHERIT_PRESETS = {
    "base": {"zh_font_name": "Serif", "font_size_ratio": 0.05, "mode": "mono", "zh_bold": True},
    "child": {"extends": "base", "zh_bold": False, "primary_lang": "en"},
    "grandchild": {"extends": "child", "en_font_name": "Arial"},
    "orphan": {"extends": "nope"},
    "loop_a": {"extends": "loop_b"},
    "loop_b": {"extends": "loop_a"},
}


def test_preset_inherit_no_override():
    """子预设只写 extends 引用：全用父预设值（无覆盖）。"""
    cfg = Config({"style": {"preset": "child"}}, None, _INHERIT_PRESETS)
    s = cfg.style_config()
    assert s["zh_font_name"] == "Serif"
    assert s["mode"] == "mono"
    assert s["zh_bold"] is False          # 子预设显式键
    assert s["preset"] == "child"        # [style] 引用键保留


def test_preset_inherit_partial_override():
    """子预设继承 + 覆盖部分键：未写字段用父值，显式键覆盖。"""
    cfg = Config({"style": {"preset": "child"}}, None, _INHERIT_PRESETS)
    s = cfg.style_config()
    assert s["zh_bold"] is False           # 子预设显式覆盖
    assert s["primary_lang"] == "en"     # 子预设新增键
    assert s["font_size_ratio"] == 0.05    # 未覆盖用父值
    assert s["mode"] == "mono"           # 未覆盖用父值


def test_preset_inherit_chain():
    """链式继承：grandchild -> child -> base，各层键按覆盖顺序生效。"""
    cfg = Config({"style": {"preset": "grandchild"}}, None, _INHERIT_PRESETS)
    s = cfg.style_config()
    assert s["zh_font_name"] == "Serif"     # 来自 base
    assert s["zh_bold"] is False            # 来自 child（覆盖 base）
    assert s["primary_lang"] == "en"      # 来自 child
    assert s["en_font_name"] == "Arial"   # 来自 grandchild
    assert s["font_size_ratio"] == 0.05     # 链上定义
    assert "outline" not in s                # 链上未定义（直接构造无内置默认）
    assert s["preset"] == "grandchild"      # 顶层引用键


def test_preset_inherit_missing_parent_raises():
    cfg = Config({"style": {"preset": "orphan"}}, None, _INHERIT_PRESETS)
    with pytest.raises(RuntimeError, match="样式预设不存在: nope"):
        cfg.style_config()


def test_preset_inherit_cycle_raises():
    cfg = Config({"style": {"preset": "loop_a"}}, None, _INHERIT_PRESETS)
    with pytest.raises(RuntimeError, match="样式预设循环继承: loop_a -> loop_b -> loop_a"):
        cfg.style_config()


def test_preset_inherit_full_override():
    """全量覆盖：不写 extends 即独立预设，与既有行为一致。"""
    cfg = Config({"style": {"preset": "base"}}, None, _INHERIT_PRESETS)
    s = cfg.style_config()
    assert s["zh_font_name"] == "Serif"
    assert s["zh_bold"] is True
    assert "primary_lang" not in s or s.get("primary_lang") != "en"


def test_style_config_cli_preset_with_inheritance():
    """CLI --preset 指向继承型预设：展开含父级键，[style] 显式键仍覆盖。"""
    cfg = Config({"style": {"preset": "base", "font_size_ratio": 0.09}}, None, _INHERIT_PRESETS)
    s = cfg.style_config(preset="child")
    assert s["preset"] == "child"
    assert s["zh_font_name"] == "Serif"    # 继承自 base
    assert s["zh_bold"] is False           # 来自 child
    assert s["font_size_ratio"] == 0.09     # [style] 显式键仍覆盖


def test_style_real_load_preset_inherit(monkeypatch, tmp_path):
    """真实 load_config 路径：presets.toml 内部继承生效，[style] 引用键保留。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "presets.toml").write_text(
        '[base]\nzh_font_name = "Serif"\nzh_bold = true\nmode = "mono"\n'
        '[plex]\nextends = "base"\nzh_bold = false\nprimary_lang = "en"\n',
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text('[style]\npreset = "plex"\n', encoding="utf-8")
    s = load_config().style_config()
    assert s["zh_font_name"] == "Serif"   # 继承自 base
    assert s["zh_bold"] is False          # plex 覆盖
    assert s["primary_lang"] == "en"    # plex 新增
    assert s["mode"] == "mono"          # 继承自 base
    assert s["preset"] == "plex"        # [style] 引用键


def test_style_real_load_preset_inherit_chain(monkeypatch, tmp_path):
    """真实路径链式继承 + [style] 覆盖：三层覆盖顺序正确。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "presets.toml").write_text(
        '[base]\nzh_font_name = "Serif"\nmode = "mono"\n'
        '[mid]\nextends = "base"\nzh_color = "#112233"\n'
        '[top]\nextends = "mid"\nzh_color = "#445566"\n',
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(
        '[style]\npreset = "top"\nzh_color = "#AABBCC"\n', encoding="utf-8"
    )
    s = load_config().style_config()
    assert s["zh_font_name"] == "Serif"     # 来自 base
    assert s["mode"] == "mono"             # 来自 base
    assert s["zh_color"] == "#AABBCC"     # [style] 显式键最高


def test_preset_inherit_expand_result_clean():
    """展开结果不含继承控制键：preset 只来自 [style]/CLI 引用，不残留 extends。"""
    cfg = Config({"style": {"preset": "child"}}, None, _INHERIT_PRESETS)
    s = cfg.style_config()
    assert s.get("preset") == "child"      # 顶层引用键
    base = cfg._expand_preset("child")
    assert "preset" not in base              # 内部展开无控制键
    assert "extends" not in base             # 继承控制键不残留


def test_load_config_reads_presets(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "presets.toml").write_text(
        "[mypreset]\nzh_font_name = \"Serif\"\nprimary_lang = \"en\"\n",
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.presets["mypreset"]["zh_font_name"] == "Serif"
    assert cfg.style_config()["zh_font_name"] == "sans-serif"  # 未引用时不受影响


# ---------- style_config 真实 load_config 路径（回归：内置默认不得覆盖 preset） ----------


def test_style_real_load_preset_no_override(monkeypatch, tmp_path):
    """只写 preset 不覆盖：应全用 preset 值，而非内置默认。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "presets.toml").write_text(
        '[mypreset]\nzh_font_name = "Serif"\nzh_bold = true\nprimary_lang = "en"\nmode = "mono"\n',
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text('[style]\npreset = "mypreset"\n', encoding="utf-8")
    s = load_config().style_config()
    assert s["zh_font_name"] == "Serif"       # 来自 preset，而非内置默认 Noto Sans CJK SC
    assert s["zh_bold"] is True           # 来自 preset
    assert s["primary_lang"] == "en"       # 来自 preset
    assert s["mode"] == "mono"             # 来自 preset
    assert s["outline"] == 2                # preset 未定义的键补内置默认


def test_style_real_load_preset_partial_override(monkeypatch, tmp_path):
    """preset + 选择性覆盖：未写字段用 preset 值，显式键覆盖 preset。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "presets.toml").write_text(
        '[mypreset]\nzh_font_name = "Serif"\nzh_bold = true\nprimary_lang = "en"\n',
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(
        '[style]\npreset = "mypreset"\nzh_bold = false\n', encoding="utf-8"
    )
    s = load_config().style_config()
    assert s["zh_bold"] is False          # 显式覆盖
    assert s["zh_font_name"] == "Serif"       # 未写字段用 preset
    assert s["primary_lang"] == "en"


def test_style_real_load_no_preset(monkeypatch, tmp_path):
    """不写 preset，从零显式设置：显式键生效，其余补内置默认。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        '[style]\nmode = "mono"\nprimary_lang = "en"\n', encoding="utf-8"
    )
    s = load_config().style_config()
    assert s["mode"] == "mono"
    assert s["primary_lang"] == "en"
    assert s["zh_font_name"] == "sans-serif"  # 其余补内置默认


def test_style_real_load_missing_preset_raises(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('[style]\npreset = "nope"\n', encoding="utf-8")
    cfg = load_config()
    with pytest.raises(RuntimeError, match="样式预设不存在: nope"):
        cfg.style_config()
