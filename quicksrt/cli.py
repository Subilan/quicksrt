"""quicksrt CLI：YouTube 视频 -> 中文硬字幕烧录。

每个环节是独立子命令，产物落盘 work/<video_id>/，支持断点续跑：
  quicksrt download <url>   下载视频
  quicksrt extract          提取 16k 音频
  quicksrt upload           上传 OSS 并生成预签名 URL
  quicksrt transcribe       阿里云 ASR 转写（英文）
  quicksrt translate        DeepSeek 翻译为简体中文
  quicksrt srt              生成 SRT 字幕
  quicksrt burn             烧录字幕（libass，不重编码音频）
  quicksrt all <url>        全链路执行
"""

from __future__ import annotations

from pathlib import Path

import typer

from . import util
from .config import Config, load_config
from .steps import burn as burn_step
from .steps import download as download_step
from .steps import extract as extract_step
from .steps import refine as refine_step
from .steps import srt as srt_step
from .steps import transcribe as transcribe_step
from .steps import translate as translate_step
from .steps import upload as upload_step

app = typer.Typer(help="quicksrt: YouTube 视频下载 -> ASR -> 翻译 -> 烧录中文硬字幕", no_args_is_help=True)


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
    typer.echo(f"video_id: {video_id}")


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
    typer.echo("extract 完成")


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
    typer.echo("upload 完成")


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
    typer.echo(f"transcribe 完成: {len(segs)} 条句子")


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
    typer.echo(f"translate 完成: {len(segs)} 条字幕")


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
    typer.echo(f"srt 完成: {path}")


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
    typer.echo(f"refine 完成: {path}")


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
    typer.echo(f"burn 完成: {out}")


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
    typer.echo(f"全链路完成: {out}")


@app.command()
def status(
    video_id: str | None = typer.Option(None, "--video-id", "-i"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
):
    """查看某个视频的流水线状态"""
    cfg = _cfg(config)
    workdir = _workdir(cfg, video_id)
    meta = util.load_meta(workdir)
    if not meta:
        typer.echo(f"work 目录 {workdir} 无 meta.json")
        return
    typer.echo(f"video_id : {meta.get('video_id', '-')}")
    typer.echo(f"title    : {meta.get('title', '-')}")
    typer.echo(f"url      : {meta.get('url', '-')}")
    typer.echo(f"时长     : {meta.get('duration', 0):.1f}s")
    steps = meta.get("steps", {})
    for name in ("download", "extract", "upload", "transcribe", "translate", "refine", "srt", "burn"):
        mark = "✓" if steps.get(name) == "done" else "·"
        typer.echo(f"  {mark} {name}")
    if meta.get("audio_url"):
        typer.echo(f"audio_url: {meta['audio_url'][:80]}...")


@app.command()
def clean(
    video_id: str | None = typer.Option(None, "--video-id", "-i"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
):
    """删除某个视频的 work 目录（全部中间产物）"""
    cfg = _cfg(config)
    workdir = _workdir(cfg, video_id)
    if not yes:
        typer.confirm(f"确认删除 {workdir}？", abort=True)
    import shutil

    shutil.rmtree(workdir)
    typer.echo(f"已删除 {workdir}")


def _reset_step(workdir: Path, step: str) -> None:
    meta = util.load_meta(workdir)
    if meta:
        meta["steps"] = {**meta.get("steps", {}), step: None}
        util.save_meta(workdir, meta)


if __name__ == "__main__":
    app()
