# 配置文件详解

quicksrt 的配置分三层：

| 文件 | 作用 | 说明 |
| --- | --- | --- |
| `config.toml` | 业务配置（下载/ASR/翻译/样式/烧录等） | 从 `config.toml.example` 复制而来，密钥不写在这里 |
| `presets.toml` | 样式预设库 | `[style]` 通过 `preset = "预设名"` 引用 |
| `.env` | 密钥（环境变量） | 从 `.env.example` 复制而来，勿提交 git |

优先级：**环境变量 > `config.toml` > `presets.toml` > 内置默认值**。`config.toml` 中没写的键取内置默认，内置默认值定义在 `quicksrt/config.py` 的 `DEFAULT_TOML`。

---

## 密钥（.env / 环境变量）

| 变量 | 用途 | 备注 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 阿里云百炼（ASR 转写） | 北京/新加坡地域的 Key 不通用，需与 `[asr] region` 一致 |
| `DASHSCOPE_WORKSPACE_ID` | 百炼工作空间 ID | 北京地域拼接 endpoint 时需要（见 `[asr] endpoint`） |
| `OSS_ACCESS_KEY_ID` | 阿里云 OSS AccessKey ID | 音频上传 |
| `OSS_ACCESS_KEY_SECRET` | 阿里云 OSS AccessKey Secret | 音频上传 |
| `DEEPSEEK_API_KEY` | DeepSeek（翻译） | — |

`.env` 文件放在项目根目录，与 `config.toml` 同级；也可以用 `export` 方式设置系统环境变量，效果相同。

---

## config.toml

### `[project]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `work_dir` | `"work"` | 工作目录。每个视频的中间产物在 `work/<video_id>/` 下（见[产物结构](project-structure.md)），支持相对或绝对路径 |
| `output_dir` | `"dist"` | 成品输出目录。烧录视频（`<标题>.mp4`）与预览 PNG（`<标题>_preview_*.png`）都写到这里 |

### `[download]`

yt-dlp 下载参数。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `format` | `"bv*+ba/b"` | yt-dlp 格式选择。`bv*+ba/b` = 最佳视频流 + 最佳音频流，自动合并为 mp4 |
| `cookies_from_browser` | `"chrome"` | 从浏览器读取 cookies 绕过反爬；留空则不读。取值为浏览器名（chrome/safari/edge 等，同 yt-dlp `--cookies-from-browser`） |
| `remote_components` | `"ejs:github"` | yt-dlp 远程组件，用于解决 JS challenge 类反爬；留空则不用 |

### `[asr]`

阿里云百炼非实时语音识别（Qwen3-ASR-Flash-Filetrans，异步文件转写）。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `provider` | `"aliyun"` | ASR 提供商，当前仅支持 `aliyun` |
| `model` | `"qwen3-asr-flash-filetrans"` | 转写模型。异步文件转写，支持字级时间戳，最长 12 小时音频 |
| `region` | `"cn-beijing"` | 百炼地域。**注意**：北京/新加坡地域的 API Key 不通用 |
| `endpoint` | `""` | API endpoint。留空时自动拼接为 `https://{DASHSCOPE_WORKSPACE_ID}.{region}.maas.aliyuncs.com/api/v1`（此时必须设置 `DASHSCOPE_WORKSPACE_ID` 环境变量） |
| `language` | `"en"` | 源语言。本链路为英文 → 简体中文，保持 `en` |
| `enable_words` | `true` | 启用字级时间戳。refine 拆句按字符数比例分配时间需要它 |
| `enable_itn` | `false` | 逆文本正则化（把口语数字/标点规范为书面形式）。字幕场景关闭，保留原文说法 |
| `poll_interval` | `5` | 轮询任务状态的间隔（秒） |
| `poll_timeout` | `7200` | 轮询超时（秒）。超长音频转写很慢，按需调大 |

### `[oss]`

音频上传到私有 OSS bucket，生成预签名 URL 供 ASR 服务拉取，用完即弃。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `bucket` | （必填） | OSS bucket 名称 |
| `endpoint` | `"oss-cn-beijing.aliyuncs.com"` | OSS endpoint，与 bucket 所在地域一致 |
| `upload_prefix` | `"audio"` | 对象 key 前缀。上传路径为 `{upload_prefix}/{video_id}.wav` |
| `presign_days` | `7` | 预签名 URL 有效期（天）。需覆盖整个 ASR 转写过程 |

### `[translate]`

DeepSeek 翻译为简体中文。使用结构化输出（`json_object` 模式），每批独立落盘断点续跑。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `provider` | `"deepseek"` | 翻译提供商，当前仅支持 `deepseek` |
| `base_url` | `"https://api.deepseek.com"` | API base URL |
| `model` | `"deepseek-chat"` | 模型名 |
| `temperature` | `0.3` | 采样温度，越低越稳定 |
| `batch_max_chars` | `3000` | 每个翻译批次的最大字符数。按批次切分、并发翻译、缓存落盘 |
| `max_retries` | `3` | 单批次失败重试次数 |
| `max_concurrency` | `4` | 并发翻译的批次数（DeepSeek 限流宽松，默认 4） |
| `context_template` | `""` | 翻译上下文模板，渲染后注入 system prompt。占位符取 meta.json 字段（`{title}` `{description}` `{uploader}` `{url}` 等），缺失渲染为空串。用于统一专有名词译法、理解视频内容；**修改模板会触发整段重译**。留空不附加 |

### `[srt]`

生成 SRT 时的文本规范化。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `max_line_chars` | `42` | 单行最大字符数。超过时在标点/空格处断为多行（最多 `max_lines` 行） |
| `max_lines` | `2` | 单条字幕最多行数 |
| `min_duration` | `1.0` | 字幕时长下限（秒）。短于此的句子拉长到该值（中文显示下限 1s） |
| `max_duration` | `0` | 字幕时长上限（秒）。`<=0` 表示不截断（ASR 时长即真实语音时长，截断会让字幕在语音未完时消失） |

### `[refine]`

显示层后处理（拆句/标点/接缝），输出双语 `refined.json`，不修改 `segments_en/zh.json` 原始数据。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `min_gap` | `0.35` | 前后接缝优化：相邻字幕间隔小于该值（秒）时，前一条延伸至后一条开始，消除闪烁 |
| `strip_end_punct` | `true` | 去掉字幕末尾的句号（中文字幕惯例不带句号） |
| `max_chars` | `42` | 拆句优化：单条字幕文本超过该长度（字符）时，在分句标点（`，、；`）处拆成多条；拆出条目时间按字符数比例从原句时间中分配 |
| `split_on_space` | `false` | 是否把空格（半角/全角）也作为中文分句分隔符；不设或 `false` 则仅在分句标点处拆句 |

### `[style]`

烧录样式（libass / ASS 风格）。详细字段如下，也可通过 `preset` 引用 `presets.toml` 中的命名预设。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `preset` | （无） | 样式预设名（见 presets.toml）。预设为基底，本段显式字段覆盖预设；不写则只用本段 |
| `zh_font_name` | `"sans-serif"` | 中文主字体。三种写法：fontconfig 通用家族名（`sans-serif`，不依赖系统安装的特定字体，渲染时回退系统默认中文字体）；变体全名精确指定字重/斜体（如 `"IBM Plex Sans SemiBold"` / `"IBM Plex Sans Italic"`）；fontconfig 模式（如 `"Heiti SC:style=Medium,weight=500"`，精确匹配字重/斜体，内部转成字体全名 `Family Style` 形式供 libass 匹配） |
| `font_size_ratio` | `0.05` | 主字号相对视频高度的比例。如 1080p 视频字号 = 1080 × 0.05 = 54px |
| `margin_v_ratio` | `0.05` | 垂直边距相对视频高度的比例 |
| `zh_color` | `"#FFFFFF"` | 中文主色，CSS 颜色：`rgb()`/`rgba()`/`#HEX`（如 `#FFFFFF`、`rgba(255,255,255,1)`） |
| `outline_color` | `"#000000"` | 描边颜色 |
| `outline` | `2` | 描边宽度（像素，随分辨率缩放） |
| `shadow` | `1` | 阴影深度 |
| `mode` | `"bilingual"` | 语言模式：`bilingual`（双语，主语言在上、副语言在下）\| `mono`（单语，只显示主语言） |
| `primary_lang` | `"zh"` | 主语言：`zh` \| `en`（主语言在上、大字号；`en` 时双语为英文在上、中文在下） |
| `zh_bold` | `false` | 中文假粗体（不依赖字体变体；要精确字重直接填 `zh_font_name` 变体全名） |
| `zh_italic` | `false` | 中文假斜体（同上） |
| `zh_italic_shear` | `""` | 中文假斜体倾角：libass `\fax` 剪切值（剪切角正切），建议范围 -2~2（对应约 ±63°，0 为垂直）；超出范围仍可解析但字形严重变形；正数向右倾、负数向左。留空用 libass 默认假斜体；**设置后自动关闭 Italic 标志**（避免双重倾斜） |
| `en_font_name` | `"sans-serif"` | 英文独立字体（写法同 `zh_font_name`；英文做主/副语言均生效，字号跟随主/副位置） |
| `en_font_ratio` | `0.6` | 英文字号相对主字号的比例（副语言行用） |
| `en_bold` | `false` | 英文假粗体 |
| `en_italic` | `false` | 英文假斜体 |
| `en_color` | `""` | 英文独立颜色（CSS 颜色）；留空时英文跟随 `zh_color` |
| `en_italic_shear` | `""` | 英文假斜体倾角（同 `zh_italic_shear`，作用于英文） |
| `bg_enabled` | `false` | 字幕背景：libass `BorderStyle=3` box，背景由渲染器按文本实际渲染范围绘制（严格贴合文本，非全宽），随字幕显示；开启后阴影/描边配置被忽略（背景替代其可读性作用） |
| `bg_color` | `"rgba(0, 0, 0, 0.5)"` | 背景颜色（CSS 颜色，如 `rgba(0,0,0,0.5)` 半透明黑，或 `#00000080`）；透明度已按 box 双层叠加效应做平方根校正，配置值即最终视觉效果 |
| `bg_padding_ratio` | `0.35` | 背景内边距相对字号的比例 |

### `[preview]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `background` | `"black"` | 预览背景色（ffmpeg color 源支持的颜色名或 `#RRGGBB`）。CLI `--background` 可临时覆盖 |

### `[burn]`

烧录编码参数。音频流一律 copy 不重编码。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `encoder` | `""` | 覆盖编码器（`libx264`/`libx265`/`libsvtav1`）。留空按源视频编码器自动选择（h264→libx264、hevc→libx265、av1→libsvtav1，未知回退 libx264）；CLI `--encoder` 优先于此 |
| `crf` | `""` | CRF 质量值。留空用各编码器默认（libx264: 18、libx265: 22、svt-av1: 30）；设置后对所有编码器生效 |
| `preset` | `""` | 编码速度/质量 preset。留空用默认（libx264: slow、libx265: medium、svt-av1: 8）；设置后对所有编码器生效（svt-av1 的 preset 只接受数字 0-13） |

---

## presets.toml

样式预设库：每个 `[段名]` 是一个命名预设，字段与 `[style]` 一致（`preset = "段名"` 引用）。内置示例（可自由增删改）：

- `[default]`：默认样式（白字黑描边、双语、微软雅黑系）
- `[plex]`：`extends = "default"`，中文用 IBM Plex Sans SC 中等字重，英文用 IBM Plex Sans 真斜体 + 蓝色
- `[plex_yellow]`：`extends = "plex"`，仅把英文颜色改为黄色
- `[cinema]`：`extends = "default"`，单语、中文为主、宋体 + 真斜体
- `[dianshiju]`：`extends = "default"`，单语、中文为主、Heiti SC 中等字重 + 粗体

### 预设继承

预设之间可相互继承，预设内写 `extends = "父预设名"`，以父预设为基底、自身键覆盖，支持链式继承（A 继承 B 继承 C）。循环继承（A→B→A）或引用不存在的预设会报错。

三种覆盖形态：

- **无覆盖**：只写 `extends = "父预设名"`，全用父值
- **部分覆盖**：继承 + 修改部分键（如 `[plex_yellow]` 只改 `en_color`）
- **全量覆盖**：不写 `extends`，独立定义全部字段

合并优先级：`config.toml [style]` 显式键 > 预设展开（继承链逐层覆盖）> 内置默认。

### 字体名写法

`zh_font_name` / `en_font_name` 支持三种写法（详见 `[style]` 表）：

1. **通用家族名**：`sans-serif` / `serif` / `monospace`，不依赖具体安装，渲染时回退系统默认
2. **变体全名**：如 `"IBM Plex Sans SemiBold"`、`"STHeitiSC-Medium"`（PostScript 名），libass 按字体全名/PostScript 名精确匹配
3. **fontconfig 模式**：如 `"Heiti SC:style=Medium,weight=500,slant=italic"`，`style`/`weight`/`slant` 可组合；`weight >= 600` 或 `slant = italic/oblique` 自动设置粗体/斜体标志
