"""配置加载：config.toml（业务配置）+ 环境变量（密钥）+ presets.toml（样式预设）。

优先级：环境变量 > config.toml > presets.toml > 内置默认值。
支持 .env 文件（仅当存在时读取，便于本地开发）。

样式预设：presets.toml 每段一个命名预设（段名 = 预设名），字段与 [style] 一致；
[style] 中写 preset = "预设名" 引用，预设为基底、[style] 显式键覆盖，
不写 preset 则只用 [style]（内置默认）。
预设之间支持相互继承：预设内写 preset = "父预设名"，以父预设为基底、自身键覆盖，
支持链式继承（A 继承 B 继承 C），循环继承与引用不存在的预设会报错。
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
# 是否把空格（半角/全角）也作为中文分句分隔符；不设或 false 则仅在分句标点处拆句
split_on_space = false

[style]
# 中文主字体（默认 sans-serif：fontconfig 通用家族名，不依赖系统安装的特定字体；可填变体全名精确指定字重/斜体，如 "IBM Plex Sans SemiBold" / "IBM Plex Sans Italic"）
zh_font_name = "sans-serif"
font_size_ratio = 0.05
margin_v_ratio = 0.05
# 中文主色（CSS 颜色：rgb()/rgba()/#HEX，如 #FFFFFF / rgba(255,255,255,1)）
zh_color = "#FFFFFF"
outline_color = "#000000"
outline = 2
shadow = 1
# 语言模式：bilingual（双语，主语言在上、副语言在下）| mono（单语，只显示主语言）
mode = "bilingual"
# 主语言（在上、大字号）：zh | en（en 时双语为英文在上、中文在下）
primary_lang = "zh"
# 中文假粗体/假斜体（不依赖字体变体；精确字重/斜体直接填 zh_font_name 变体全名）
zh_bold = false
zh_italic = false
# 中文假斜体倾角（libass \\fax 剪切值，正数向右倾、负数向左；留空用 libass 默认假斜体，设置后自动关闭 Italic 标志）
zh_italic_shear = ""
# 英文独立字体与样式（英文做主/副语言均生效，字号跟随主/副位置）
en_font_name = "sans-serif"
en_font_ratio = 0.6
en_bold = false
en_italic = false
en_italic_shear = ""
# 英文独立颜色（CSS 颜色：rgb()/rgba()/#HEX）；留空时英文跟随 zh_color
en_color = ""
# 字幕背景：全宽半透明矩形条（分层渲染：背景 Dialogue + 文本 Dialogue）
# bg_color 为 CSS 颜色（如 rgba(0,0,0,0.5) 半透明黑，或 #00000080）；bg_padding_ratio 为内边距相对字号比例
bg_enabled = false
bg_color = "rgba(0, 0, 0, 0.5)"
bg_padding_ratio = 0.35

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
    def __init__(
        self,
        raw: dict,
        path: Path | None = None,
        presets: dict | None = None,
        user_style: dict | None = None,
    ):
        self.raw = raw
        self.path = path
        self.presets = presets or {}
        # config.toml 中用户显式写的 [style] 键（区别于内置默认填充的键）
        self.user_style = user_style if user_style is not None else dict(raw.get("style", {}))

    @property
    def work_dir(self) -> Path:
        return Path(self.raw["project"]["work_dir"])

    @property
    def output_dir(self) -> Path:
        return Path(self.raw["project"]["output_dir"])

    def section(self, name: str) -> dict:
        return self.raw.get(name, {})

    def _expand_preset(self, name: str, stack: tuple[str, ...] = ()) -> dict:
        """递归展开 presets.toml 预设：preset 键引用父预设为基底、自身键覆盖。

        支持链式继承；循环继承（A -> B -> A）与引用不存在的预设报错。
        展开结果不含继承控制键（preset），只含纯样式字段。
        """
        raw = self.presets.get(name)
        if raw is None:
            names = ", ".join(sorted(self.presets)) or "无（可创建 presets.toml）"
            raise RuntimeError(f"样式预设不存在: {name}（可用: {names}）")
        if name in stack:
            raise RuntimeError(f"样式预设循环继承: {' -> '.join((*stack, name))}")
        own = dict(raw)
        parent = own.pop("preset", None)
        if parent:
            base = self._expand_preset(parent, (*stack, name))
            return {**base, **own}
        return own

    def style_config(self, preset: str | None = None) -> dict:
        """展开 [style]：preset（presets.toml，预设间可继承）为基底，config.toml 显式键覆盖，其余补内置默认。

        优先级：config.toml 显式键 > 预设展开 > 内置默认。
        preset 参数非 None 时临时切换预设（如 CLI --preset），仅影响本次调用，不改 config.toml。
        """
        explicit = dict(self.user_style)
        if preset is not None:
            explicit["preset"] = preset
        preset_name = explicit.get("preset")
        if preset_name:
            base = self._expand_preset(preset_name)
            style = {**base, **explicit}
        else:
            style = explicit
        defaults = dict(self.section("style"))
        defaults.pop("preset", None)
        return {**defaults, **style}

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
    user_style: dict = {}
    if cfg_path.exists():
        user_raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        user_style = dict(user_raw.get("style", {}))
        raw = _deep_merge(raw, user_raw)
    else:
        print(f"[config] 未找到 {cfg_path}，使用内置默认配置")

    presets: dict = {}
    presets_path = cfg_path.parent / "presets.toml"
    if presets_path.exists():
        presets = tomllib.loads(presets_path.read_text(encoding="utf-8"))
    else:
        print(f"[config] 未找到 {presets_path}，样式预设不可用")
    return Config(raw, cfg_path, presets, user_style=user_style)


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
