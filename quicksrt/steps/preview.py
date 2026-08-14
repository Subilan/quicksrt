"""preview：纯色背景上渲染单条字幕的高分辨率 PNG 预览（字幕样式预览）。

不依赖视频：ffmpeg lavfi color 源生成背景帧，叠加按目标分辨率 PlayRes 生成的 ASS，
字号/边距按目标分辨率比例计算，预览即"烧进该分辨率视频"的效果。
分辨率预设 720p/1080p/4k，或 auto（源视频分辨率，需 video.mp4 存在）。
默认渲染第一条字幕，--index 可指定任意条；语言模式取 [style] 配置。
CLI 加 --inline-image 时生成 iTerm2 内联图片转义序列，终端内直接展示。
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

from .. import util
from . import burn

RESOLUTIONS = {"720p": (1280, 720), "1080p": (1920, 1080), "4k": (3840, 2160)}


def resolve_size(res: str, workdir: Path) -> tuple[int, int, str]:
    """解析分辨率参数 -> (width, height, 输出标签)。"""
    r = res.lower()
    if r in RESOLUTIONS:
        w, h = RESOLUTIONS[r]
        return w, h, r
    if r == "auto":
        video = workdir / "video.mp4"
        if not video.exists():
            raise FileNotFoundError(
                f"缺少视频文件: {video}（auto 需要源视频分辨率，可指定 720p/1080p/4k）"
            )
        probe = util.probe_video(video)
        return probe["width"], probe["height"], f"{probe['width']}x{probe['height']}"
    raise RuntimeError(f"不支持的分辨率: {res}（可选: auto/720p/1080p/4k）")


def pick_item(items: list[dict], index: int) -> dict:
    """取第 index 条（从 1 开始）并归一化时间到首帧，保证渲染可见。"""
    if index < 1 or index > len(items):
        raise RuntimeError(f"--index 超出范围: {index}（共 {len(items)} 条）")
    return {**items[index - 1], "start": 0.0, "end": 1.0}


def inline_image_escape(path: Path, width: str = "100%") -> str:
    """生成 iTerm2 内联图片转义序列（协议见 iterm2.com/documentation-images.html）。"""
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"\x1b]1337;File=inline=1;width={width}:" + b64 + "\a"


def run(cfg, workdir: Path, log: logging.Logger, res: str = "auto", index: int = 1) -> Path:
    meta = util.load_meta(workdir)
    refined_path = workdir / "refined.json"
    if not refined_path.exists():
        raise FileNotFoundError(f"缺少 {refined_path.name}（先执行 refine）")
    items = json.loads(refined_path.read_text(encoding="utf-8"))
    if not items:
        raise RuntimeError("refined.json 为空，无法预览")

    width, height, res_label = resolve_size(res, workdir)
    item = pick_item(items, index)

    style_cfg = cfg.style_config()
    mode, primary_lang = burn._style_mode(style_cfg)
    ass = burn.build_ass_items(
        [item], style_cfg, {"width": width, "height": height}, mode=mode, primary_lang=primary_lang
    )
    ass_path = workdir / "preview.ass"
    ass_path.write_text(ass, encoding="utf-8")

    out_dir = cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    title = re.sub(r'[\\/:*?"<>|\s]+', "_", meta.get("title", workdir.name)).strip("_")[:80]
    output = out_dir / f"{title}_preview_{res_label}.png"

    bg = cfg.section("preview").get("background", "black")
    color_src = f"color=c={bg}:s={width}x{height}:d=1"
    # filter 内路径用单引号包裹，防止空格/特殊字符问题
    filter_str = f"ass=filename='{str(ass_path).replace(chr(39), chr(92) + chr(39))}'"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", color_src,
        "-vf", filter_str, "-frames:v", "1", str(output),
    ]
    log.info(
        "[preview] %s（%dx%d, mode=%s primary=%s, 第 %d 条）-> %s",
        res_label, width, height, mode, primary_lang, index, output,
    )
    util.run_cmd(cmd, log, timeout=None)
    return output
