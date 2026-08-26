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
        "mode": "bilingual", "primary_lang": "zh", "zh_fake_bold": False,
    },
    "bold_en": {
        "zh_font_name": "Arial", "primary_lang": "en",
        "en_font_name": "Arial", "en_fake_bold": True, "en_fake_italic": True,
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
    assert style["en_fake_bold"] is True             # 预设展开
    assert style["en_fake_italic"] is True           # 预设展开
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
    assert style["en_fake_bold"] is True               # 来自 --preset 的 bold_en
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


def test_load_config_reads_presets(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "presets.toml").write_text(
        "[mypreset]\nzh_font_name = \"Serif\"\nprimary_lang = \"en\"\n",
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.presets["mypreset"]["zh_font_name"] == "Serif"
    assert cfg.style_config()["zh_font_name"] == "Noto Sans CJK SC"  # 未引用时不受影响


# ---------- style_config 真实 load_config 路径（回归：内置默认不得覆盖 preset） ----------


def test_style_real_load_preset_no_override(monkeypatch, tmp_path):
    """只写 preset 不覆盖：应全用 preset 值，而非内置默认。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "presets.toml").write_text(
        '[mypreset]\nzh_font_name = "Serif"\nzh_fake_bold = true\nprimary_lang = "en"\nmode = "mono"\n',
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text('[style]\npreset = "mypreset"\n', encoding="utf-8")
    s = load_config().style_config()
    assert s["zh_font_name"] == "Serif"       # 来自 preset，而非内置默认 Noto Sans CJK SC
    assert s["zh_fake_bold"] is True           # 来自 preset
    assert s["primary_lang"] == "en"       # 来自 preset
    assert s["mode"] == "mono"             # 来自 preset
    assert s["outline"] == 2                # preset 未定义的键补内置默认


def test_style_real_load_preset_partial_override(monkeypatch, tmp_path):
    """preset + 选择性覆盖：未写字段用 preset 值，显式键覆盖 preset。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "presets.toml").write_text(
        '[mypreset]\nzh_font_name = "Serif"\nzh_fake_bold = true\nprimary_lang = "en"\n',
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(
        '[style]\npreset = "mypreset"\nzh_fake_bold = false\n', encoding="utf-8"
    )
    s = load_config().style_config()
    assert s["zh_fake_bold"] is False          # 显式覆盖
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
    assert s["zh_font_name"] == "Noto Sans CJK SC"  # 其余补内置默认


def test_style_real_load_missing_preset_raises(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('[style]\npreset = "nope"\n', encoding="utf-8")
    cfg = load_config()
    with pytest.raises(RuntimeError, match="样式预设不存在: nope"):
        cfg.style_config()
