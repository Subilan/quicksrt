"""subtitle：下载视频 -> 语音识别 -> 翻译 -> 优化 -> 输出中英两个 SRT。"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import time
from pathlib import Path

import requests

from .config import Config
from . import util

SENTENCE_END = "。！？.!?…"
CLAUSE_END = "，、；：,;:"
_TRIM_CHARS = " \t\u3000。，、；：！？….,!?;:\"'”’）)】」』"

ASR_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
LANG_NAMES = {"zh": "简体中文", "en": "英文"}


# ---------- 视频输入 ----------


def _download(url: str, cfg: Config, log: logging.Logger) -> Path:
    import yt_dlp

    opts = {
        "format": cfg.subtitle.get("format", "bv*+ba/b"),
        "outtmpl": str(cfg.work_dir / "%(id)s" / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 20,
    }
    cookies = str(cfg.subtitle.get("cookies_from_browser", "")).strip()
    if cookies:
        opts["cookiesfrombrowser"] = (cookies,)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    video_id = info["id"]
    log.info("下载完成: %s (%s)", info.get("title") or video_id, video_id)
    files = [p for p in (cfg.work_dir / video_id).glob(f"{video_id}.*") if p.suffix in (".mp4", ".mkv", ".webm", ".mov")]
    if not files:
        raise RuntimeError(f"下载后找不到视频文件: {cfg.work_dir / video_id}")
    return files[0]


def _resolve_video(source: str, cfg: Config, log: logging.Logger) -> Path:
    if source.startswith(("http://", "https://")):
        return _download(source, cfg, log)
    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"视频不存在: {path}")
    return path


# ---------- 语音识别 ----------


def _silences(wav: Path) -> list[tuple[float, float]]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af", "silencedetect=noise=-35dB:d=0.3", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    return list(zip(starts, ends))


def _plan_chunks(duration: float, silences: list[tuple[float, float]], limit: float) -> list[tuple[float, float]]:
    """按静音位置切分音频，每段不超过 limit 秒（找不到静音则硬切）。"""
    if duration <= limit:
        return [(0.0, duration)]
    cuts: list[float] = []
    pos = 0.0
    while duration - pos > limit:
        low, high = pos + limit * 0.4, pos + limit
        candidates = [(s + e) / 2 for s, e in silences if low <= (s + e) / 2 <= high]
        cut = candidates[-1] if candidates else high
        cuts.append(cut)
        pos = cut
    bounds = [0.0, *cuts, duration]
    return list(zip(bounds, bounds[1:]))


def _find_sentence(data: dict) -> list[dict]:
    node: object = data
    for _ in range(3):
        if isinstance(node, dict) and "sentence" in node:
            node = node["sentence"]
            break
        if isinstance(node, dict):
            node = node.get("output") or {}
        else:
            break
    if isinstance(node, dict):
        return [node]
    if isinstance(node, list):
        return node
    raise RuntimeError(f"ASR 响应中没有识别结果: {json.dumps(data, ensure_ascii=False)[:500]}")


def _asr_chunk(mp3: Path, asr: dict, log: logging.Logger) -> list[dict]:
    uri = "data:audio/mpeg;base64," + base64.b64encode(mp3.read_bytes()).decode()
    payload = {
        "model": asr["model"],
        "input": {"messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": uri}}]}]},
        "parameters": {"format": "mp3", "sample_rate": "16000"},
    }
    url = asr["base_url"].rstrip("/") + ASR_PATH
    headers = {"Authorization": f"Bearer {asr['api_key']}"}
    resp = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=600)
        except requests.RequestException as exc:
            log.warning("ASR 请求异常（第 %d 次）: %s", attempt, exc)
            time.sleep(2**attempt)
            continue
        if resp.status_code == 200:
            break
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("ASR HTTP %d（第 %d 次）: %s", resp.status_code, attempt, resp.text[:200])
            time.sleep(2**attempt)
            continue
        raise RuntimeError(f"ASR 调用失败 (HTTP {resp.status_code}): {resp.text[:500]}")
    if resp is None or resp.status_code != 200:
        raise RuntimeError(f"ASR 调用失败: {resp.text[:500] if resp is not None else '无响应'}")

    words = []
    for sentence in _find_sentence(resp.json()):
        for word in sentence.get("words") or []:
            text = (word.get("text") or "") + (word.get("punctuation") or "")
            if not text.strip():
                continue
            words.append(
                {
                    "start": int(word["begin_time"]) / 1000,
                    "end": int(word["end_time"]) / 1000,
                    "text": text,
                }
            )
    return words


def _transcribe(video: Path, work: Path, asr: dict, log: logging.Logger) -> list[dict]:
    cached = util.read_json(work / "asr.json")
    if cached:
        log.info("复用识别结果: %s", work / "asr.json")
        return cached

    wav = work / "audio.wav"
    work.mkdir(parents=True, exist_ok=True)
    if not wav.exists():
        log.info("提取音频: %s", video.name)
        util.run_cmd(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(wav)]
        )
    duration = util.probe_video(video)["duration"]
    chunks = _plan_chunks(duration, _silences(wav), float(asr.get("chunk_seconds", 240)))
    log.info("音频 %.1fs，切分 %d 段", duration, len(chunks))

    words: list[dict] = []
    for idx, (start, end) in enumerate(chunks):
        cache = work / "asr" / f"{idx:03d}.json"
        data = util.read_json(cache)
        if data is None:
            mp3 = work / "asr" / f"{idx:03d}.mp3"
            mp3.parent.mkdir(parents=True, exist_ok=True)
            util.run_cmd(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
                 "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "64k", "-ar", "16000", "-ac", "1", str(mp3)]
            )
            log.info("识别第 %d/%d 段（%.1fs ~ %.1fs）", idx + 1, len(chunks), start, end)
            data = _asr_chunk(mp3, asr, log)
            util.write_json(cache, data)
            mp3.unlink(missing_ok=True)
        words.extend({"start": w["start"] + start, "end": w["end"] + start, "text": w["text"]} for w in data)

    util.write_json(work / "asr.json", words)
    wav.unlink(missing_ok=True)
    log.info("识别完成: %d 个词", len(words))
    return words


# ---------- 断句与优化 ----------


def _strip_end(text: str) -> str:
    return text.rstrip(_TRIM_CHARS)


_LATIN = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def _tidy_latin(text: str) -> str:
    """英文逗号/句号后补空格（ASR 的词条常把标点紧贴在词尾）。"""
    text = re.sub(r"([,;:!?])(?=[A-Za-z0-9])", r"\1 ", text)
    return re.sub(r"\.(?=[A-Za-z])", ". ", text)


def _join_tokens(tokens: list[str]) -> str:
    """拼回句子：拉丁字母/数字之间补空格，中文之间不补。"""
    out = ""
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if out and out[-1] in _LATIN and token[0] in _LATIN:
            out += " "
        out += token
    return out


def _detect_lang(words: list[dict]) -> str:
    text = "".join(w["text"] for w in words)
    han = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return "zh" if han > len(text) * 0.2 else "en"


def _build_cues(words: list[dict], max_seconds: float, split_gap: float) -> list[dict]:
    """按句末标点、较长停顿与最长时长把小词序列合成字幕条。"""
    cues: list[dict] = []
    buf: list[dict] = []

    def flush() -> None:
        if not buf:
            return
        text = _strip_end(_tidy_latin(_join_tokens([w["text"] for w in buf])))
        if text:
            cues.append({"start": round(buf[0]["start"], 3), "end": round(buf[-1]["end"], 3), "text": text})
        buf.clear()

    for word in words:
        if buf and (word["start"] - buf[-1]["end"] >= split_gap or word["end"] - buf[0]["start"] > max_seconds):
            flush()
        buf.append(word)
        tail = word["text"].rstrip()[-1:]
        if tail in SENTENCE_END:
            flush()
        elif tail in CLAUSE_END and word["end"] - buf[0]["start"] >= max_seconds * 0.6:
            flush()
    flush()
    return cues


def _merge_seams(cues: list[dict], min_gap: float) -> int:
    """把相邻字幕间小于 min_gap 的空隙（含轻微重叠）直接填平，避免闪烁。"""
    filled = 0
    for current, following in zip(cues, cues[1:]):
        if following["start"] <= current["start"]:
            continue
        if following["start"] - current["end"] < min_gap:
            current["end"] = following["start"]
            filled += 1
    return filled


# ---------- 翻译 ----------


def _system_prompt(source: str, target: str) -> str:
    return (
        f"你是字幕翻译。用户给出 JSON 数组，每项形如 {{\"id\": 1, \"text\": \"...\"}}，text 是{LANG_NAMES[source]}字幕。"
        f"把每条 text 翻译成{LANG_NAMES[target]}。相邻条目是连续语句，必要时参考上下文，但必须逐条对应。"
        f"要求：条数、顺序、id 与输入完全一致，不合并、不拆分、不遗漏；译文简洁口语化；"
        f"只输出 JSON，不要解释。输出格式：{{\"translations\": [{{\"id\": 1, \"text\": \"译文\"}}]}}"
    )


def _translate_batch(batch: list[dict], prompt: str, tr: dict, log: logging.Logger) -> dict[int, str]:
    url = tr["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {tr['api_key']}", "Content-Type": "application/json"}
    payload = {
        "model": tr["model"],
        "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    expected = {item["id"] for item in batch}
    last_error = ""
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=300)
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                time.sleep(2 * attempt)
                continue
            content = (resp.json()["choices"][0]["message"]["content"] or "").strip()
            data = json.loads(content)
            items = data["translations"] if isinstance(data, dict) else data
            result = {int(item["id"]): str(item["text"]).strip() for item in items}
            if set(result) != expected:
                last_error = f"返回 id 不匹配（期望 {len(expected)} 条，实际 {len(result)} 条）"
                time.sleep(2 * attempt)
                continue
            return result
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            last_error = str(exc)
            time.sleep(2 * attempt)
    raise RuntimeError(f"翻译失败（{len(batch)} 条）: {last_error}")


def _translate(texts: list[str], source: str, target: str, tr: dict, log: logging.Logger) -> list[str]:
    prompt = _system_prompt(source, target)
    batch_size = int(tr.get("batch_size", 20))
    result: dict[int, str] = {}
    for start in range(0, len(texts), batch_size):
        batch = [{"id": i, "text": texts[i]} for i in range(start, min(start + batch_size, len(texts)))]
        result.update(_translate_batch(batch, prompt, tr, log))
        log.info("翻译 %d/%d 条", min(start + batch_size, len(texts)), len(texts))
    return [_strip_end(result[i]) for i in range(len(texts))]


# ---------- SRT ----------


def _write_srt(path: Path, cues: list[dict], key: str) -> None:
    blocks = [
        f"{idx}\n{util.fmt_srt_ts(cue['start'])} --> {util.fmt_srt_ts(cue['end'])}\n{cue[key]}\n"
        for idx, cue in enumerate(cues, start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")


def run(source: str, cfg: Config) -> list[Path]:
    log = util.setup_logging()
    video = _resolve_video(source, cfg, log)
    work = cfg.work_dir / video.stem

    cues = util.read_json(work / "cues.json")
    if cues:
        log.info("复用字幕结果: %s", work / "cues.json")
    else:
        words = _transcribe(video, work, cfg.asr, log)
        if not words:
            raise RuntimeError("没有识别到任何语音")
        cues = _build_cues(
            words,
            float(cfg.subtitle.get("max_cue_seconds", 7)),
            float(cfg.subtitle.get("split_gap", 0.8)),
        )
        source_lang = _detect_lang(words)
        target_lang = "en" if source_lang == "zh" else "zh"
        log.info("识别语言 %s，翻译为 %s，共 %d 条字幕", source_lang, target_lang, len(cues))
        translated = _translate([cue["text"] for cue in cues], source_lang, target_lang, cfg.translate, log)
        for cue, text in zip(cues, translated):
            zh_text = cue["text"] if source_lang == "zh" else text
            en_text = cue["text"] if source_lang == "en" else text
            cue["zh"] = zh_text
            cue["en"] = en_text[:1].upper() + en_text[1:]
            cue.pop("text")
        filled = _merge_seams(cues, float(cfg.subtitle.get("min_gap", 0.3)))
        log.info("接缝优化: 填平 %d 处", filled)
        util.write_json(work / "cues.json", cues)

    zh_path = video.with_name(f"{video.stem}.zh.srt")
    en_path = video.with_name(f"{video.stem}.en.srt")
    _write_srt(zh_path, cues, "zh")
    _write_srt(en_path, cues, "en")
    log.info("生成字幕: %s (%d 条)", zh_path.name, len(cues))
    log.info("生成字幕: %s (%d 条)", en_path.name, len(cues))
    return [zh_path, en_path]
