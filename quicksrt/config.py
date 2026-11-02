"""配置加载：config.toml 与 presets.toml。"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    asr: dict = field(default_factory=dict)
    translate: dict = field(default_factory=dict)
    subtitle: dict = field(default_factory=dict)
    style: dict = field(default_factory=dict)
    burn: dict = field(default_factory=dict)

    def section(self, name: str) -> dict:
        return getattr(self, name)

    @property
    def work_dir(self) -> Path:
        return Path(self.subtitle.get("work_dir", "work"))


def _load_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _resolve_preset(name: str, presets: dict, seen: tuple[str, ...] = ()) -> dict:
    if name in seen:
        raise RuntimeError(f"presets.toml 预设继承成环: {' -> '.join(seen + (name,))}")
    preset = presets.get(name)
    if preset is None:
        raise RuntimeError(f"presets.toml 缺少预设 {name!r}")
    parent = preset.get("extends")
    base = _resolve_preset(parent, presets, seen + (name,)) if parent else {}
    return {**base, **{k: v for k, v in preset.items() if k != "extends"}}


def load_config(path: Path | str = "config.toml") -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"缺少配置文件: {path}")
    data = _load_toml(path)

    style = dict(data.get("style") or {})
    preset_name = style.pop("preset", "default")
    presets_path = path.with_name("presets.toml")
    if not presets_path.exists():
        raise FileNotFoundError(f"缺少样式预设文件: {presets_path}")
    style = {**_resolve_preset(str(preset_name), _load_toml(presets_path)), **style}

    return Config(
        asr=data.get("asr") or {},
        translate=data.get("translate") or {},
        subtitle=data.get("subtitle") or {},
        style=style,
        burn=data.get("burn") or {},
    )


def cli_config(path: str) -> Config:
    try:
        return load_config(path)
    except (OSError, tomllib.TOMLDecodeError, RuntimeError) as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
