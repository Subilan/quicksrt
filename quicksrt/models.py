"""统一的时间轴字幕数据模型。

链路中所有环节都通过这个模型交换数据：
ASR 原始结果 -> 英文 segments -> 中文 segments -> SRT 渲染。
时间单位统一为秒（浮点）。

pydantic 模型：落盘/加载时自动序列化与校验，words 缺失走默认值，
类型或结构不符时给出精确报错（原手写 from_dict/to_dict 已由
model_validate/model_dump 替代）。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class Word(BaseModel):
    """词级时间戳。"""

    text: str
    start: float
    end: float


class Segment(BaseModel):
    """一条字幕。"""

    id: int
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)


def load_segments(path: Path) -> list[Segment]:
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        return [Segment.model_validate(d) for d in data]
    except ValidationError as e:
        raise RuntimeError(f"{path.name} 数据不符合 Segment 结构: {e}") from e


def save_segments(path: Path, segments: list[Segment]) -> None:
    path.write_text(
        json.dumps([s.model_dump() for s in segments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
