"""quicksrt CLI：YouTube 视频 -> 中文硬字幕烧录。

每个环节是独立子命令，产物落盘 work/<video_id>/，支持断点续跑：
  quicksrt download <url>   下载视频
  quicksrt extract          提取 16k 音频
  quicksrt upload           上传 OSS 并生成预签名 URL
  quicksrt transcribe       阿里云 ASR 转写（英文）
  quicksrt translate        DeepSeek 翻译为简体中文
  quicksrt srt              生成 SRT 字幕
  quicksrt burn             烧录字幕（libass，不重编码音频）
  quicksrt preview          纯色背景渲染单条字幕 PNG 预览（字幕样式预览；--crop 输出紧贴文字的裁剪图；--example 用内置示例文本，不依赖已有数据；--preset a,b / --all-preset 批量样式预览）
  quicksrt all <url>        全链路执行
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from . import util
from .config import Config, load_config
from .steps import burn as burn_step
from .steps import download as download_step
from .steps import extract as extract_step
from .steps import preview as preview_step
from .steps import refine as refine_step
from .steps import srt as srt_step
from .steps import transcribe as transcribe_step
from .steps import translate as translate_step
from .steps import upload as upload_step

app = typer.Typer(help="quicksrt: YouTube 视频下载 -> ASR -> 翻译 -> 烧录中文硬字幕", no_args_is_help=True)


@app.callback()
def _main(
    no_color: bool = typer.Option(False, "--no-color", help="禁用终端日志颜色"),
):
    """全局选项（置于子命令前，如 quicksrt --no-color preview）。"""
    if no_color:
        os.environ["QUICKSRT_NO_COLOR"] = "1"
    # 基础终端日志：保证全程序输出（含 config 加载提示、子命令参数校验警告）
    # 都走标准日志；文件日志由各子命令在确定 workdir 后补充。
    util.setup_logging()


def _cfg(config_path: Path) -> Config:
    return load_config(config_path)


def _workdir(cfg, video_id: str | None) -> Path:
    if video_id:
        return cfg.work_dir / video_id
    latest = util.find_latest_workdir(cfg)
    if latest is None:
        raise typer.BadParameter("work 目录为空，请先执行 download 或指定 --video-id")
    return latest


@app.command()
def download(
    url: str,
    config: Path = typer.Option(Path("config.toml"), "--config", "-c", help="配置文件路径"),
    force: bool = typer.Option(False, "--force", "-f", help="忽略断点强制重跑"),
    fmt: str | None = typer.Option(None, "--format", help="yt-dlp 格式选择（覆盖 config.toml）"),
):
    """下载 YouTube 视频到 work/<video_id>/video.mp4"""
    cfg = _cfg(config)
    workdir = cfg.work_dir
    log = util.setup_logging(workdir)
    if force:
        _reset_step(workdir, download_step.STEP)
    video_id = download_step.run(url, cfg, workdir, log, fmt=fmt)
    log.info("video_id: %s", video_id)


@app.command()
def extract(
    video_id: str | None = typer.Option(None, "--video-id", "-i", help="视频 ID（默认取最新的 work 目录）"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """从视频提取 16kHz 单声道 WAV 音频"""
    cfg = _cfg(config)
    workdir = _workdir(cfg, video_id)
    log = util.setup_logging(workdir)
    if force:
        _reset_step(workdir, extract_step.STEP)
    extract_step.run(cfg, workdir, log)
    log.info("extract 完成")


@app.command()
def upload(
    video_id: str | None = typer.Option(None, "--video-id", "-i"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """上传音频到 OSS，生成预签名 URL"""
    cfg = _cfg(config)
    workdir = _workdir(cfg, video_id)
    log = util.setup_logging(workdir)
    if force:
        _reset_step(workdir, upload_step.STEP)
    upload_step.run(cfg, workdir, log)
    log.info("upload 完成")


@app.command()
def transcribe(
    video_id: str | None = typer.Option(None, "--video-id", "-i"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """阿里云 ASR 转写（英文），产出 segments_en.json"""
    cfg = _cfg(config)
    workdir = _workdir(cfg, video_id)
    log = util.setup_logging(workdir)
    if force:
        _reset_step(workdir, transcribe_step.STEP)
    segs = transcribe_step.run(cfg, workdir, log, force=force)
    log.info("transcribe 完成: %d 条句子", len(segs))


@app.command()
def translate(
    video_id: str | None = typer.Option(None, "--video-id", "-i"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """DeepSeek 翻译为简体中文，产出 segments_zh.json"""
    cfg = _cfg(config)
    workdir = _workdir(cfg, video_id)
    log = util.setup_logging(workdir)
    if force:
        _reset_step(workdir, translate_step.STEP)
    segs = translate_step.run(cfg, workdir, log, force=force)
    log.info("translate 完成: %d 条字幕", len(segs))


@app.command()
def srt(
    video_id: str | None = typer.Option(None, "--video-id", "-i"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """由中文 segments 生成规范化 subs.srt（refined.json 存在时输出双语）"""
    cfg = _cfg(config)
    workdir = _workdir(cfg, video_id)
    log = util.setup_logging(workdir)
    if force:
        _reset_step(workdir, srt_step.STEP)
    path = srt_step.run(cfg, workdir, log, force=force)
    log.info("srt 完成: %s", path)


@app.command()
def refine(
    video_id: str | None = typer.Option(None, "--video-id", "-i"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """显示层后处理：拆句/标点/接缝优化，产出 refined.json（双语）"""
    cfg = _cfg(config)
    workdir = _workdir(cfg, video_id)
    log = util.setup_logging(workdir)
    if force:
        _reset_step(workdir, refine_step.STEP)
    path = refine_step.run(cfg, workdir, log, force=force)
    log.info("refine 完成: %s", path)


@app.command()
def burn(
    video_id: str | None = typer.Option(None, "--video-id", "-i"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    force: bool = typer.Option(False, "--force", "-f"),
    encoder: str | None = typer.Option(None, "--encoder", "-e", help="覆盖编码器（libx264/libx265/libsvtav1）"),
):
    """烧录字幕到视频，输出到 dist/"""
    cfg = _cfg(config)
    workdir = _workdir(cfg, video_id)
    log = util.setup_logging(workdir)
    if force:
        _reset_step(workdir, burn_step.STEP)
    out = burn_step.run(cfg, workdir, log, force=force, encoder=encoder)
    log.info("burn 完成: %s", out)


@app.command()
def all(
    url: str,
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    force: bool = typer.Option(False, "--force", "-f", help="忽略所有断点强制重跑"),
):
    """全链路：download -> extract -> upload -> transcribe -> translate -> refine -> srt -> burn"""
    cfg = _cfg(config)
    log = util.setup_logging(cfg.work_dir)
    if force:
        log.warning("--force 会清空所有断点状态")
    video_id = download_step.run(url, cfg, cfg.work_dir, log)
    workdir = cfg.work_dir / video_id
    if force:
        for step in (extract_step, upload_step, transcribe_step, translate_step, refine_step, srt_step, burn_step):
            _reset_step(workdir, step.STEP)
    extract_step.run(cfg, workdir, log)
    upload_step.run(cfg, workdir, log)
    transcribe_step.run(cfg, workdir, log)
    translate_step.run(cfg, workdir, log)
    refine_step.run(cfg, workdir, log)
    srt_step.run(cfg, workdir, log)
    out = burn_step.run(cfg, workdir, log)
    log.info("全链路完成: %s", out)


@app.command()
def preview(
    video_id: str | None = typer.Option(None, "--video-id", "-i"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    res: str = typer.Option("auto", "--res", help="输出分辨率: auto/720p/1080p/4k（auto 取源视频分辨率；--example 模式无源视频，auto 回退 1080p）"),
    index: int = typer.Option(1, "--index", help="渲染第几条字幕（从 1 开始，默认 1）"),
    background: str | None = typer.Option(None, "--background", help="预览背景色（覆盖 [preview] background，ffmpeg color 支持的值，如 white/#202020；crop 时作为截取背景色，不指定则沿用 [preview] background 默认值）"),
    preset: str | None = typer.Option(None, "--preset", help="样式预设名（逗号分隔多个，如 plex,plex_yellow；覆盖 config.toml [style] 的 preset，仅本次预览生效）"),
    all_preset: bool = typer.Option(False, "--all-preset", help="批量渲染 presets.toml 中所有样式预设的效果；与 --preset 互斥"),
    inline: bool = typer.Option(False, "--inline-image", help="在 iTerm2 终端内直接展示预览图（多预设时并排对比）"),
    crop: bool = typer.Option(False, "--crop", help="只渲染文字本身（输出紧贴文字范围的 PNG，无背景帧）；此时 --res/--video-id/--background 无效"),
    example: str | None = typer.Option(None, "--example", metavar="lorem|glass|fox", help="用内置固定示例文本预览（lorem/glass/fox，默认 lorem），不依赖已有 work 数据；与 --video-id 互斥"),
):
    """渲染单条字幕 PNG 预览（语言模式取 [style] 配置；--preset 逗号分隔多个或 --all-preset 批量样式预览；--crop 输出紧贴文字的裁剪图；--example 用固定示例文本直接预览，不依赖已有数据）"""
    cfg = _cfg(config)
    # 先初始化基础日志：参数校验阶段的警告也要走标准日志；
    # 文件日志待 workdir 确定后再补充。
    log = util.setup_logging()
    if example is not None:
        if example not in preview_step.EXAMPLES:
            raise typer.BadParameter(f"未知示例: {example}（可选: {', '.join(preview_step.EXAMPLES)}）")
        if video_id is not None:
            log.warning("--example 模式下 --video-id 无效，忽略")
            video_id = None
        if res == "auto":
            res = "1080p"  # 示例模式无源视频可探测，回退 1080p
        workdir = None
    else:
        if crop:
            if video_id is not None:
                log.warning("--crop 模式下 --video-id 无效，改用最新 work 目录")
            if res != "auto":
                log.warning("--crop 模式下 --res 无效，忽略")
            video_id, res = None, "auto"
            # --background 在 crop 下仍生效（截取背景色，不指定则用 [preview] background）
        workdir = _workdir(cfg, video_id)
    util.setup_logging(workdir)  # 补充文件日志（crop 时落在最新 work 目录）
    try:
        presets = preview_step.resolve_presets(cfg, preset, all_preset)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if presets is None:
        outputs = [(None, preview_step.run(cfg, workdir, log, res=res, index=index, background=background, crop=crop, example=example))]
    else:
        outputs = [
            (p, preview_step.run(cfg, workdir, log, res=res, index=index, background=background, crop=crop, preset=p, example=example))
            for p in presets
        ]
    if inline:
        if os.environ.get("TERM_PROGRAM") != "iTerm.app":
            log.warning("当前终端不是 iTerm2，内联图片可能无法显示")
        # 内联图片是 iTerm2 终端协议转义序列（功能性数据，非日志消息），
        # 保持原样写 stdout，不走日志（日志会加前缀/换行破坏协议）。
        # 每张图独占一行（100% 宽度）；多预设时预设名单独一行，图片在下一行
        for name, out in outputs:
            if name:
                typer.echo(f"[{name}]")
            typer.echo(preview_step.inline_image_escape(out))
            typer.echo()
    for _, out in outputs:
        log.info("preview 完成: %s", out)


@app.command()
def status(
    video_id: str | None = typer.Option(None, "--video-id", "-i"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
):
    """查看某个视频的流水线状态"""
    cfg = _cfg(config)
    workdir = _workdir(cfg, video_id)
    log = util.setup_logging(workdir)
    meta = util.load_meta(workdir)
    if not meta:
        log.warning("work 目录 %s 无 meta.json", workdir)
        return
    log.info("video_id : %s", meta.get("video_id", "-"))
    log.info("title    : %s", meta.get("title", "-"))
    log.info("url      : %s", meta.get("url", "-"))
    log.info("时长     : %.1fs", meta.get("duration", 0))
    steps = meta.get("steps", {})
    for name in ("download", "extract", "upload", "transcribe", "translate", "refine", "srt", "burn"):
        mark = "✓" if steps.get(name) == "done" else "·"
        log.info("  %s %s", mark, name)
    if meta.get("audio_url"):
        log.info("audio_url: %s...", meta["audio_url"][:80])


@app.command()
def clean(
    video_id: str | None = typer.Option(None, "--video-id", "-i"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
):
    """删除某个视频的 work 目录（全部中间产物）"""
    cfg = _cfg(config)
    workdir = _workdir(cfg, video_id)
    log = util.setup_logging(workdir)
    if not yes:
        typer.confirm(f"确认删除 {workdir}？", abort=True)
    import shutil

    shutil.rmtree(workdir)
    log.info("已删除 %s", workdir)


def _reset_step(workdir: Path, step: str) -> None:
    meta = util.load_meta(workdir)
    if meta:
        meta["steps"] = {**meta.get("steps", {}), step: None}
        util.save_meta(workdir, meta)


def _normalize_example_argv(argv: list[str]) -> list[str]:
    """把 preview 子命令的裸 --example 规范化为 --example lorem（默认示例）。

    typer/click 的选项要么必须带值、要么是纯 flag，无法表达"可选值选项"
    （--example 可带可不带值），故在解析前做一次规范化：仅当子命令为 preview
    且 --example 后没有值时补默认值 lorem。--example glass / --example=glass 原样保留。
    """
    try:
        sub_idx = next(i for i, a in enumerate(argv) if not a.startswith("-"))
    except StopIteration:
        return argv
    if argv[sub_idx] != "preview":
        return argv
    out: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if i > sub_idx and a == "--example" and (i + 1 >= len(argv) or argv[i + 1].startswith("-")):
            out.extend(["--example", "lorem"])
        else:
            out.append(a)
        i += 1
    return out


def main(argv: list[str] | None = None) -> None:
    """CLI 入口：规范化 --example 可选值参数后启动（typer 不支持可选值选项）。"""
    args = list(sys.argv[1:] if argv is None else argv)
    app(args=_normalize_example_argv(args))


if __name__ == "__main__":
    main()
