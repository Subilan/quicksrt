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
