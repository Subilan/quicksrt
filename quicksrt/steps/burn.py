"""burn：将 SRT 渲染为 ASS 并烧录进视频（libass），音频流 copy 不重编码。

编码策略：按源视频编码器选择对应编码器（libx264/libx265/libsvtav1），
CRF 质量模式 + 慢速 preset，尽量降低二次编码损失。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .. import util

STEP = "burn"

ENCODER_DEFAULTS = {
    "libx264": {"preset": "slow", "crf": 18},
    "libx265": {"preset": "medium", "crf": 22},
    "libsvtav1": {"preset": "8", "crf": 30},
}


def _pick_encoder(video_codec: str) -> str:
    codec = video_codec.lower()
    if "h264" in codec or codec in ("avc1", "avc"):
        return "libx264"
    if codec in ("hevc", "h265"):
        return "libx265"
    if "av1" in codec:
        return "libsvtav1"
    return "libx264"


def _ass_ts(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 60_000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def build_ass(srt_path: Path, cfg_style: dict, probe: dict) -> str:
    width, height = probe["width"], probe["height"]
    fontsize = max(12, round(height * float(cfg_style.get("font_size_ratio", 0.05))))
    margin_v = round(height * float(cfg_style.get("margin_v_ratio", 0.05)))
    margin_h = round(width * 0.03)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cfg_style.get('font_name', 'Noto Sans CJK SC')},{fontsize},{cfg_style.get('primary_color', '&H00FFFFFF')},&H000000FF,{cfg_style.get('outline_color', '&H00000000')},&H80000000,0,0,0,0,100,100,0,0,1,{cfg_style.get('outline', 2)},{cfg_style.get('shadow', 1)},2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for raw in srt_path.read_text(encoding="utf-8").split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split("\n", 2)
        if len(parts) < 3:
            continue
        ts = parts[1].split(" --> ")
        if len(ts) != 2:
            continue

        def to_sec(t: str) -> float:
            hh, mm, ss = t.replace(",", ".").split(":")
            return int(hh) * 3600 + int(mm) * 60 + float(ss)

        text = _ass_escape(parts[2].replace("\r", "").replace("\n", "\\N"))
        lines.append(
            f"Dialogue: 0,{_ass_ts(to_sec(ts[0]))},{_ass_ts(to_sec(ts[1]))},Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"


def run(cfg, workdir: Path, log: logging.Logger, force: bool = False, encoder: str | None = None) -> Path:
    meta = util.load_meta(workdir)
    video = workdir / "video.mp4"
    srt_path = workdir / "subs.srt"
    if not video.exists():
        raise FileNotFoundError(f"缺少视频文件: {video}（先执行 download）")
    if not srt_path.exists():
        raise FileNotFoundError(f"缺少字幕文件: {srt_path}（先执行 srt）")

    out_dir = cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    title = re.sub(r'[\\/:*?"<>|\s]+', "_", meta.get("title", workdir.name)).strip("_")[:80]
    output = out_dir / f"{title}.mp4"

    if not force and util.step_done(meta, STEP) and output.exists():
        log.info("[burn] 已完成，跳过")
        return output

    probe = util.probe_video(video)
    log.info(
        "[burn] 源视频: %dx%d %.2ffps, %s, pix_fmt=%s",
        probe["width"], probe["height"], probe.get("fps") or 0,
        probe["video_codec"], probe["pix_fmt"],
    )
    if probe.get("pix_fmt", "").lower() not in ("", "yuv420p"):
        log.warning("[burn] 源像素格式 %s 非 yuv420p，输出将转为 yuv420p（兼容性优先）", probe["pix_fmt"])

    ass_path = workdir / "subs.ass"
    ass_path.write_text(build_ass(srt_path, cfg.section("style"), probe), encoding="utf-8")

    enc = encoder or _pick_encoder(probe["video_codec"])
    if enc not in ENCODER_DEFAULTS:
        raise RuntimeError(f"不支持的编码器: {enc}（可选: {', '.join(ENCODER_DEFAULTS)}）")
    defaults = ENCODER_DEFAULTS[enc]
    burn_cfg = cfg.section("burn")
    preset = burn_cfg.get("preset") or defaults["preset"]
    crf = burn_cfg.get("crf") or defaults["crf"]

    # filter 内路径用单引号包裹，防止空格/特殊字符问题
    filter_str = f"ass=filename='{str(ass_path).replace(chr(39), chr(92) + chr(39))}'"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-vf", filter_str,
        "-c:v", enc, "-crf", str(crf), "-preset", str(preset),
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(output),
    ]
    log.info("[burn] 编码器 %s crf=%s preset=%s -> %s", enc, crf, preset, output)
    util.run_cmd(cmd, log, timeout=None)

    meta["steps"] = {**meta.get("steps", {}), STEP: "done"}
    meta["burn"] = {"encoder": enc, "crf": crf, "preset": preset}
    util.save_meta(workdir, meta)
    log.info("[burn] 完成: %s", output)
    return output
