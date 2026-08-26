"""preview：纯色背景上渲染单条字幕的高分辨率 PNG 预览（字幕样式预览）。

不依赖视频：ffmpeg lavfi color 源生成背景帧，叠加按目标分辨率 PlayRes 生成的 ASS，
字号/边距按目标分辨率比例计算，预览即"烧进该分辨率视频"的效果。
分辨率预设 720p/1080p/4k，或 auto（源视频分辨率，需 video.mp4 存在）。
默认渲染第一条字幕，--index 可指定任意条；语言模式取 [style] 配置。
CLI 加 --inline-image 时生成 iTerm2 内联图片转义序列，终端内直接展示。

--crop：不渲染背景帧，纯色背景渲染后按非背景色包围盒裁剪，输出紧贴文字、
保留纯色背景的 PNG。截取背景色：--background 优先，否则取 [preview] background（默认 black）；
背景色需与文字颜色有足够差异（--res/--video-id 无效）。

--example <lorem|glass|fox>：用内置固定示例文本（中英对照）直接预览，不依赖
refined.json 等已有数据、无需 work 目录；裸 --example 默认 lorem。
lorem 中文用《临江仙·滚滚长江东逝水》上阕（与 lorem ipsum 行宽相当）；glass 对应"我能吞下玻璃而不伤身体"（I can eat glass）；
fox 对应经典全字母句"quick brown fox"。示例模式无源视频，--res auto 回退 1080p。
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

# --example 内置示例文本（中英对照；lorem 无中文对应，中文用滚滚长江东逝水）
EXAMPLES = {
    "lorem": {
        "zh": "滚滚长江东逝水，浪花淘尽英雄。是非成败转头空。青山依旧在，几度夕阳红。",
        "en": "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
    },
    "glass": {
        "zh": "我能吞下玻璃而不伤身体",
        "en": "I can eat glass and it doesn't hurt me.",
    },
    "fox": {
        "zh": "那只敏捷的棕色狐狸跳过了一只懒狗",
        "en": "The quick brown fox jumps over the lazy dog.",
    },
}

# crop 渲染画布：用 4K 画布渲染后裁剪到文字包围盒，保证文字渲染分辨率（清晰度）足够高；
# 画布大小影响渲染字号（字号随高度比例），最终裁剪图尺寸 = 文字实际像素
_CROP_CANVAS_W, _CROP_CANVAS_H = 3840, 2160

# bbox 滤镜日志：... w:.. h:.. crop=W:H:X:Y drawbox=..
_BBOX_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


def _parse_bbox(stderr: str) -> tuple[int, int, int, int] | None:
    """从 bbox 滤镜日志解析文字包围盒 -> (w, h, x, y)，取最后一次 crop= 输出。"""
    m = None
    for m in _BBOX_RE.finditer(stderr):
        pass
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))


def resolve_size(res: str, workdir: Path | None) -> tuple[int, int, str]:
    """解析分辨率参数 -> (width, height, 输出标签)。"""
    r = res.lower()
    if r in RESOLUTIONS:
        w, h = RESOLUTIONS[r]
        return w, h, r
    if r == "auto":
        if workdir is None:
            raise FileNotFoundError(
                "缺少视频文件（auto 需要源视频分辨率，可指定 720p/1080p/4k）"
            )
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


def _scratch_dir(cfg, workdir: Path | None) -> Path:
    """ASS 等中间文件目录：有 workdir 用 workdir，否则用 output_dir（如 --example 模式）。"""
    return workdir if workdir is not None else cfg.output_dir


def _output_title(meta: dict, workdir: Path | None) -> str:
    """输出文件名标题：meta.title 优先，否则 workdir 名；清洗非法字符。"""
    title = meta.get("title") or (workdir.name if workdir is not None else "preview")
    return re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")[:80] or "preview"


def _preset_tag(preset: str | None) -> str:
    """文件名中的预设后缀（无预设时为空）：多预设渲染时避免产物互相覆盖。"""
    return f"_{preset}" if preset else ""


def resolve_presets(cfg, preset: str | None, all_preset: bool = False) -> list[str] | None:
    """解析 --preset/--all-preset -> 预设名列表；两者都未指定时返回 None（用 [style] 配置）。

    --preset 支持逗号分隔多个（如 "plex,plex_yellow"）；--all-preset 取 presets.toml
    全部预设；两者互斥。预设不存在、全部预设为空时报 ValueError。
    """
    if all_preset and preset:
        raise ValueError("--all-preset 与 --preset 互斥，不能同时使用")
    if all_preset:
        names = sorted(cfg.presets)
        if not names:
            raise ValueError("presets.toml 中无任何样式预设（可创建 presets.toml）")
        return names
    if preset:
        names = [p.strip() for p in preset.split(",") if p.strip()]
        if not names:
            raise ValueError("--preset 值为空")
        for name in names:
            if name not in cfg.presets:
                available = ", ".join(sorted(cfg.presets)) or "无（可创建 presets.toml）"
                raise ValueError(f"样式预设不存在: {name}（可用: {available}）")
        return names
    return None


def _run_crop(cfg, workdir: Path | None, log: logging.Logger, items: list[dict],
              index: int, meta: dict, preset: str | None = None,
              background: str | None = None) -> Path:
    """纯色背景渲染单条字幕，按非背景色包围盒裁剪，输出保留该纯色背景的 PNG。

    截取背景色：background 参数优先，否则取 [preview] background（默认 black）。
    背景色需与文字颜色有足够差异，否则包围盒探测失败。
    """
    item = pick_item(items, index)
    style_cfg = cfg.style_config(preset=preset)
    mode, primary_lang = burn._style_mode(style_cfg)
    probe = {"width": _CROP_CANVAS_W, "height": _CROP_CANVAS_H}
    ass = burn.build_ass_items(
        [item], style_cfg, probe, mode=mode, primary_lang=primary_lang
    )
    scratch = _scratch_dir(cfg, workdir)
    ass_path = scratch / "preview_crop.ass"
    scratch.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(ass, encoding="utf-8")

    out_dir = cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    title = _output_title(meta, workdir)
    tag = _preset_tag(preset)
    raw_png = out_dir / f"{title}_preview{tag}_crop_raw.png"
    output = out_dir / f"{title}_preview{tag}_crop.png"
    bg = background or cfg.section("preview").get("background", "black")
    try:
        # 1. 纯色背景渲染（ass 滤镜只写 RGB 不写 alpha，故截取背景 + 非背景色探测）
        color_src = f"color=c={bg}:s={_CROP_CANVAS_W}x{_CROP_CANVAS_H}:d=1"
        ass_filter = f"ass=filename='{str(ass_path).replace(chr(39), chr(92) + chr(39))}'"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", color_src,
            "-vf", ass_filter,
            "-frames:v", "1", str(raw_png),
        ]
        util.run_cmd(cmd, log, timeout=None)
        # 2. 探测文字包围盒（背景色抠图转 alpha -> bbox 检测非透明像素范围）
        proc = util.run_cmd(
            ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(raw_png),
             "-vf", f"colorkey=color={bg}:similarity=0.05:blend=0,"
                     "format=rgba,alphaextract,bbox",
             "-f", "null", "-"],
            log, timeout=None,
        )
        bounds = _parse_bbox(proc.stderr)
        if bounds is None:
            raise RuntimeError("crop: 未能检测到文字范围（渲染结果为空？）")
        w, h, x, y = bounds
        # 3. 裁剪到文字范围（保留纯色背景，渲染原样，无抠图边缘问题）
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(raw_png), "-vf", f"crop={w}:{h}:{x}:{y}", str(output),
        ]
        util.run_cmd(cmd, log, timeout=None)
    finally:
        raw_png.unlink(missing_ok=True)
    log.info(
        "[preview] crop（第 %d 条，%dx%d, mode=%s primary=%s）-> %s",
        index, w, h, mode, primary_lang, output,
    )
    return output


def _render_frame(cfg, workdir: Path | None, log: logging.Logger, items: list[dict],
                  index: int, meta: dict, res: str, background: str | None,
                  preset: str | None) -> Path:
    """纯色背景帧 + 单条字幕渲染（普通预览）。"""
    width, height, res_label = resolve_size(res, workdir)
    item = pick_item(items, index)

    style_cfg = cfg.style_config(preset=preset)
    mode, primary_lang = burn._style_mode(style_cfg)
    ass = burn.build_ass_items(
        [item], style_cfg, {"width": width, "height": height}, mode=mode, primary_lang=primary_lang
    )
    scratch = _scratch_dir(cfg, workdir)
    ass_path = scratch / "preview.ass"
    scratch.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(ass, encoding="utf-8")

    out_dir = cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    title = _output_title(meta, workdir)
    output = out_dir / f"{title}_preview{_preset_tag(preset)}_{res_label}.png"

    bg = background or cfg.section("preview").get("background", "black")
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


def _run_example(cfg, workdir: Path | None, log: logging.Logger, example: str,
                 res: str = "auto", index: int = 1, background: str | None = None,
                 crop: bool = False, preset: str | None = None) -> Path:
    """用内置固定示例文本（中英对照）渲染预览，不依赖 refined.json 等已有数据。

    示例模式无源视频，--res auto 回退 1080p；workdir 为 None 时 ASS 临时文件写入
    output_dir，渲染后清理。
    """
    text = EXAMPLES.get(example)
    if text is None:
        raise RuntimeError(f"未知示例: {example}（可选: {', '.join(EXAMPLES)}）")
    if res == "auto":
        res = "1080p"
    meta = {"title": f"example-{example}"}
    items = [{"id": 0, "start": 0.0, "end": 1.0, **text}]
    scratch = _scratch_dir(cfg, workdir)
    try:
        if crop:
            return _run_crop(cfg, workdir, log, items, index, meta,
                             preset=preset, background=background)
        return _render_frame(cfg, workdir, log, items, index, meta, res=res,
                             background=background, preset=preset)
    finally:
        # 示例模式 ASS 是临时文件，渲染后清理
        for name in ("preview.ass", "preview_crop.ass"):
            (scratch / name).unlink(missing_ok=True)


def run(cfg, workdir: Path | None, log: logging.Logger, res: str = "auto", index: int = 1,
        background: str | None = None, crop: bool = False, preset: str | None = None,
        example: str | None = None) -> Path:
    """background 为 None 时取 [preview] background（默认 black）；
    crop 时 background 作为截取背景色（未指定则取 [preview] background，默认 black），输出保留该纯色背景；
    preset 非 None 时临时切换样式预设；
    example 非 None 时用内置示例文本预览（不依赖已有数据，workdir 可为 None）。"""
    if example is not None:
        return _run_example(cfg, workdir, log, example, res=res, index=index,
                            background=background, crop=crop, preset=preset)
    meta = util.load_meta(workdir)
    refined_path = workdir / "refined.json"
    if not refined_path.exists():
        raise FileNotFoundError(f"缺少 {refined_path.name}（先执行 refine）")
    items = json.loads(refined_path.read_text(encoding="utf-8"))
    if not items:
        raise RuntimeError("refined.json 为空，无法预览")
    if crop:
        return _run_crop(cfg, workdir, log, items, index, meta, preset=preset, background=background)
    return _render_frame(cfg, workdir, log, items, index, meta, res=res,
                         background=background, preset=preset)
