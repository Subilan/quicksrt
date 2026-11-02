"""命令行入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import burn, subtitle
from .config import cli_config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="qsrt")
    parser.add_argument("-c", "--config", default="config.toml", help="配置文件路径")
    commands = parser.add_subparsers(dest="command", required=True)

    subtitle_cmd = commands.add_parser("subtitle", help="下载/识别/翻译，生成中英两个 SRT")
    subtitle_cmd.add_argument("source", help="YouTube 链接或本地视频路径")

    burn_cmd = commands.add_parser("burn", help="把视频同名的 .zh.srt / .en.srt 烧录进视频")
    burn_cmd.add_argument("video", help="视频路径")
    burn_cmd.add_argument("-o", "--output", help="输出路径（默认 <视频名>.burned.mp4）")

    args = parser.parse_args(argv)
    cfg = cli_config(args.config)
    try:
        if args.command == "subtitle":
            subtitle.run(args.source, cfg)
        else:
            burn.run(Path(args.video), cfg, Path(args.output) if args.output else None)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
