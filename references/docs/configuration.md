# 配置文件详解

quicksrt 的配置分三层：

| 文件 | 作用 | 说明 |
| --- | --- | --- |
| `config.toml` | 业务配置（下载/ASR/翻译/样式/烧录等） | 从 `config.toml.example` 复制而来，密钥不写在这里 |
| `presets.toml` | 样式预设库 | `[style]` 通过 `preset = "预设名"` 引用 |
| `.env` | 密钥（环境变量） | 从 `.env.example` 复制而来，勿提交 git |

优先级：**环境变量 > `config.toml` > `presets.toml` > 内置默认值**。`config.toml` 中没写的键取内置默认，内置默认值定义在 `quicksrt/config.py` 的 `DEFAULT_TOML`。

## 语言模型（language agnostic）

链路用「语言码」标识语言、用「角色」标识职责，二者正交：

| 角色 | 含义 | 配置位置 | 缺省 |
| --- | --- | --- | --- |
| `source` | 源语言（视频原始语言，ASR 输入） | `[asr] language` | `en` |
| `target` | 翻译目标语言 | `[translate] target_lang` | `zh` |
| `primary` | 显示主语言（在上、大字号） | `[style] primary_lang` | 取 `target` |
| `secondary` | 显示副语言（在下、小字号） | `[style] secondary_lang` | 翻译对中非主语言的那个（通常即 `source`） |

语言码为 BCP-47 风格（`en`/`zh`/`ja`/`ko`…），只作为数据出现于配置、文件名（`segments_<语言码>.json`）与 `refined.json` 字段。样式（`[style]`/`presets.toml`）只描述 primary/secondary 两个显示角色的视觉属性，**与语言无关**——任意语言组合可套用同一套样式。

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
| `language` | `"en"` | 源语言（视频原始语言，任意语言码，如 en/zh/ja/ko）。传给 ASR 服务，也是下游 segments 文件名的语言码 |
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

DeepSeek 翻译。使用结构化输出（`json_object` 模式），每批独立落盘断点续跑。语言方向：`source_lang` → `target_lang`，任意语言组合。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `provider` | `"deepseek"` | 翻译提供商，当前仅支持 `deepseek` |
| `base_url` | `"https://api.deepseek.com"` | API base URL |
| `model` | `"deepseek-chat"` | 模型名 |
| `temperature` | `0.3` | 采样温度，越低越稳定 |
| `batch_max_chars` | `3000` | 每个翻译批次的最大字符数。按批次切分、并发翻译、缓存落盘 |
| `max_retries` | `3` | 单批次失败重试次数 |
| `max_concurrency` | `4` | 并发翻译的批次数（DeepSeek 限流宽松，默认 4） |
| `source_lang` | `""` | 翻译读取的源语言。缺省取 `[asr] language`；显式设置可覆盖（读 `segments_<该码>.json`） |
| `target_lang` | `"zh"` | 翻译目标语言（任意语言码）。输出 `segments_<该码>.json` |
| `prompt_template` | `""` | 翻译提示词模板，完全自定义；缺省用内置预设模板。占位符：`{source_lang}`/`{target_lang}`（语言名，如 English/简体中文）、`{source}`/`{target}`（语言码）。内置语言名表覆盖常见语言（未知语言码回退用原码）；模板中其他字面 `{}` 原样保留。**修改模板会触发整段重译** |
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

显示层后处理（拆句/标点/接缝），输出双语 `refined.json`，不修改 `segments_<语言码>.json` 原始数据。拆句以显示主语言（`primary_lang`，须为源或目标语言之一）为准，按该语言的规则表拆句。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `min_gap` | `0.35` | 前后接缝优化：相邻字幕间隔小于该值（秒）时，前一条延伸至后一条开始，消除闪烁 |
| `strip_end_punct` | `true` | 去掉拆句主语言文本末尾的句号/逗号等（规则按语言表：zh/ja 去、拉丁系保留） |
| `max_chars` | `42` | 拆句优化：单条字幕文本超过该长度（字符）时，在分句标点处拆成多条；拆出条目时间按字符数比例从原句时间中分配 |
| `split_on_space` | `false` | 是否把空格（半角/全角）也作为分句分隔符；缺省按拆句主语言的规则表（zh 不拆、拉丁系拆） |
| `split_punct` | `""` | 拆句规则覆盖（一般不设）：分句标点集合。缺省按拆句主语言从内置语言规则表取（zh/ja/en 预置，未知语言用 Unicode 通用规则）；显式设置后覆盖语言表 |
| `strip_punct` | `""` | 同上：句尾去除的标点集合（如 `"。．，、；"`） |
| `break_after` | `""` | 同上：行内断行的后缀字符（中文虚词/日文助词），留空则按空格断 |

### `[style]`

烧录样式（libass / ASS 风格）。样式描述的是**两个显示角色**的视觉属性：`primary`（主行，在上、大字号）与 `secondary`（副行，在下、小字号），与具体语言无关；语言码（`primary_lang`/`secondary_lang`）是实例化数据，缺省链见上文「语言模型」。也可通过 `preset` 引用 `presets.toml` 中的命名预设。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `preset` | （无） | 样式预设名（见 presets.toml）。预设为基底，本段显式字段覆盖预设；不写则只用本段 |
| `primary_font_name` | `"sans-serif"` | 主行字体。三种写法：fontconfig 通用家族名（`sans-serif`，不依赖系统安装的特定字体，渲染时回退系统默认字体）；变体全名精确指定字重/斜体（如 `"IBM Plex Sans SemiBold"` / `"IBM Plex Sans Italic"`）；fontconfig 模式（如 `"Heiti SC:style=Medium,weight=500"`，精确匹配字重/斜体，内部转成字体全名 `Family Style` 形式供 libass 匹配） |
| `font_size_ratio` | `0.05` | 主行字号相对视频高度的比例。如 1080p 视频字号 = 1080 × 0.05 = 54px |
| `margin_v_ratio` | `0.05` | 垂直边距相对视频高度的比例 |
| `primary_color` | `"#FFFFFF"` | 主行颜色，CSS 颜色：`rgb()`/`rgba()`/`#HEX`（如 `#FFFFFF`、`rgba(255,255,255,1)`） |
| `outline_color` | `"#000000"` | 描边颜色 |
| `outline` | `2` | 描边宽度（像素，随分辨率缩放） |
| `shadow` | `1` | 阴影深度 |
| `mode` | `"bilingual"` | 语言模式：`bilingual`（双语，主语言在上、副语言在下）\| `mono`（单语，只显示主语言） |
| `primary_lang` | `""` | 主语言（在上、大字号）：任意语言码（BCP-47 风格，如 zh/en/ja/ko）。缺省取 `[translate] target_lang` |
| `secondary_lang` | `""` | 副语言（在下、小字号）：任意语言码。缺省取翻译对中非主语言的那个（通常即 `[asr] language` 源语言）；主语言显式设为源语言时副语言自动为目标语言 |
| `primary_bold` | `false` | 主行假粗体（不依赖字体变体；要精确字重直接填 `primary_font_name` 变体全名） |
| `primary_italic` | `false` | 主行假斜体（同上） |
| `primary_italic_shear` | `""` | 主行假斜体倾角：libass `\fax` 剪切值（剪切角正切），建议范围 -2~2（对应约 ±63°，0 为垂直）；超出范围仍可解析但字形严重变形；正数向右倾、负数向左。留空用 libass 默认假斜体；**设置后自动关闭 Italic 标志**（避免双重倾斜） |
| `secondary_font_name` | `"sans-serif"` | 副行字体（写法同 `primary_font_name`；缺省跟随主行字体） |
| `secondary_font_ratio` | `0.6` | 副行字号 = 主行字号 × 该比例 |
| `secondary_bold` | `false` | 副行假粗体 |
| `secondary_italic` | `false` | 副行假斜体 |
| `secondary_color` | `""` | 副行独立颜色（CSS 颜色）；留空时副行跟随 `primary_color` |
| `secondary_italic_shear` | `""` | 副行假斜体倾角（同 `primary_italic_shear`，作用于副行） |
| `bg` | `false` | 字幕背景：libass `BorderStyle=3` box，背景由渲染器按文本实际渲染范围绘制（严格贴合文本，非全宽），随字幕显示。布尔简写：`bg = true` 启用 / `bg = false` 禁用（缺省 `false`）；或写表 `bg = { padding, padding_x, padding_y, color }`（**出现即启用**，各键可省）：`padding` 为水平/垂直同值的内边距相对字号比例（缺省 `0.35`），`padding_x` / `padding_y` 分别覆盖水平/垂直内边距（可与 `padding` 基底混合），`color` 为背景颜色（CSS 颜色，如 `rgba(0,0,0,0.5)` 半透明黑，或 `#00000080`，缺省 `"rgba(0, 0, 0, 0.5)"`）。透明度已按 box 双层叠加效应做平方根校正，配置值即最终视觉效果。开启后阴影/描边配置被忽略（背景替代其可读性作用） |

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

样式预设库：每个 `[段名]` 是一个命名预设，字段与 `[style]` 一致（`preset = "段名"` 引用）。预设是「显示角色样式类」：只描述 `primary`（主行）与 `secondary`（副行）两个角色的视觉属性，**不含语言码**——语言码在 `config.toml [style]` 配置，同一套预设可套用任意语言组合。内置示例（可自由增删改）：

- `[default]`：默认样式（白字黑描边、双语、微软雅黑系）
- `[plex]`：`extends = "default"`，主行用 IBM Plex Sans SC 中等字重，副行用 IBM Plex Sans 真斜体 + 蓝色
- `[plex_yellow]`：`extends = "plex"`，仅把副行颜色改为黄色
- `[belike]`：`extends = "default"`，单语、红色字幕背景框
- `[dazhizuo]`：`extends = "default"`，单语、宋体 + 真斜体
- `[dianshiju]`：`extends = "default"`，单语、Heiti SC 中等字重 + 粗体

### 预设继承

预设之间可相互继承，预设内写 `extends = "父预设名"`，以父预设为基底、自身键覆盖，支持链式继承（A 继承 B 继承 C）。循环继承（A→B→A）或引用不存在的预设会报错。

三种覆盖形态：

- **无覆盖**：只写 `extends = "父预设名"`，全用父值
- **部分覆盖**：继承 + 修改部分键（如 `[plex_yellow]` 只改 `secondary_color`）
- **全量覆盖**：不写 `extends`，独立定义全部字段

合并优先级：`config.toml [style]` 显式键 > 预设展开（继承链逐层覆盖）> 内置默认。

### 字体名写法

`primary_font_name` / `secondary_font_name` 支持三种写法（详见 `[style]` 表）：

1. **通用家族名**：`sans-serif` / `serif` / `monospace`，不依赖具体安装，渲染时回退系统默认
2. **变体全名**：如 `"IBM Plex Sans SemiBold"`、`"STHeitiSC-Medium"`（PostScript 名），libass 按字体全名/PostScript 名精确匹配
3. **fontconfig 模式**：如 `"Heiti SC:style=Medium,weight=500,slant=italic"`，`style`/`weight`/`slant` 可组合；`weight >= 600` 或 `slant = italic/oblique` 自动设置粗体/斜体标志
