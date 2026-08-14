"""统一的时间轴字幕数据模型。

链路中所有环节都通过这个模型交换数据：
ASR 原始结果 -> 英文 segments -> 中文 segments -> SRT 渲染。
时间单位统一为秒（浮点）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(
            id=d["id"],
            start=float(d["start"]),
            end=float(d["end"]),
            text=d["text"],
            words=[Word(w["text"], float(w["start"]), float(w["end"])) for w in d.get("words", [])],
        )


def load_segments(path: Path) -> list[Segment]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Segment.from_dict(d) for d in data]


def save_segments(path: Path, segments: list[Segment]) -> None:
    path.write_text(
        json.dumps([s.to_dict() for s in segments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
