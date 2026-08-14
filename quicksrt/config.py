"""配置加载：config.toml（业务配置）+ 环境变量（密钥）。

优先级：环境变量 > config.toml > 内置默认值。
支持 .env 文件（仅当存在时读取，便于本地开发）。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config.toml")

DEFAULT_TOML = """
[project]
work_dir = "work"
output_dir = "dist"

[download]
format = "bv*+ba/b"
cookies_from_browser = "chrome"
remote_components = "ejs:github"

[asr]
provider = "aliyun"
model = "qwen3-asr-flash-filetrans"
region = "cn-beijing"
# endpoint 留空时按 region + 环境变量 DASHSCOPE_WORKSPACE_ID 自动拼接
endpoint = ""
language = "en"
enable_words = true
enable_itn = false
poll_interval = 5
poll_timeout = 7200

[oss]
bucket = ""
endpoint = "oss-cn-beijing.aliyuncs.com"
upload_prefix = "audio"
presign_days = 7

[translate]
provider = "deepseek"
base_url = "https://api.deepseek.com"
model = "deepseek-chat"
temperature = 0.3
batch_max_chars = 3000
max_retries = 3
# 并发翻译的批次数量（DeepSeek 限流宽松，默认 4）
max_concurrency = 4
# 翻译上下文模板：占位符取 meta.json 字段（{title} {description} {uploader} {url} 等），缺失渲染为空；留空不附加
context_template = ""

[srt]
max_line_chars = 42
max_lines = 2
# 字幕时长下限：短于此时长的句子被拉长到该值（中文显示下限 1s）
min_duration = 1.0
# 字幕时长上限：<=0 表示不截断（ASR 时长即真实语音时长，截断会让字幕在语音未完时消失）
max_duration = 0

[refine]
# 前后接缝优化：相邻字幕间隔小于该值（秒）时，前一条延伸至后一条开始，消除闪烁
min_gap = 0.35
# 标点优化：去掉字幕末尾的句号（中文字幕惯例不带句号）
strip_end_punct = true
# 拆句优化：单条字幕文本最大长度（字符），超过则在分句标点（，、；）处拆成多条；
# 拆出的条目时间按字符数比例从原句时间中分配
max_chars = 42

[style]
font_name = "Noto Sans CJK SC"
font_size_ratio = 0.05
margin_v_ratio = 0.05
primary_color = "&H00FFFFFF"
outline_color = "&H00000000"
outline = 2
shadow = 1
# 语言模式：bilingual（双语，主语言在上、副语言在下）| mono（单语，只显示主语言）
mode = "bilingual"
# 主语言（在上、大字号）：zh | en（en 时双语为英文在上、中文在下）
primary_lang = "zh"
# 中文样式（粗体/斜体）
font_bold = false
font_italic = false
# 英文独立字体与样式（英文做主/副语言均生效，字号跟随主/副位置）
en_font_name = "Noto Sans CJK SC"
en_font_ratio = 0.6
en_bold = false
en_italic = false

[preview]
# 预览背景色（ffmpeg color 源支持的颜色名或 #RRGGBB）
background = "black"

[burn]
crf = ""
preset = ""
"""


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class Config:
    def __init__(self, raw: dict, path: Path | None = None):
        self.raw = raw
        self.path = path

    @property
    def work_dir(self) -> Path:
        return Path(self.raw["project"]["work_dir"])

    @property
    def output_dir(self) -> Path:
        return Path(self.raw["project"]["output_dir"])

    def section(self, name: str) -> dict:
        return self.raw.get(name, {})

    @property
    def asr_endpoint(self) -> str:
        asr = self.section("asr")
        endpoint = asr.get("endpoint", "").strip()
        if endpoint:
            return endpoint.rstrip("/")
        workspace_id = os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip()
        if not workspace_id:
            raise RuntimeError(
                "未配置 ASR endpoint。请在 config.toml 设置 [asr] endpoint，"
                "或设置环境变量 DASHSCOPE_WORKSPACE_ID 以便按 region 自动拼接"
            )
        region = asr.get("region", "cn-beijing")
        return f"https://{workspace_id}.{region}.maas.aliyuncs.com/api/v1"


def load_config(path: str | Path | None = None) -> Config:
    _load_dotenv()
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict = tomllib.loads(DEFAULT_TOML)
    if cfg_path.exists():
        user_raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        raw = _deep_merge(raw, user_raw)
    else:
        print(f"[config] 未找到 {cfg_path}，使用内置默认配置")
    return Config(raw, cfg_path)


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
