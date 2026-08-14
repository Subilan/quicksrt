# quicksrt

YouTube 视频 → 中文硬字幕烧录的一站式管线。每一环都是独立命令，产物落盘支持断点续跑。

```
下载(yt-dlp) → 提取音频(ffmpeg) → 上传OSS(预签名URL) → ASR转写(阿里云百炼) → 翻译(DeepSeek) → refine(拆句/标点/接缝/双语) → 生成SRT → 烧录(libass)
```

## 环境要求

- uv（Python 项目管理）
- ffmpeg ≥ 8，需带 libass（`brew install homebrew-ffmpeg/ffmpeg/ffmpeg`，core 版不带 libass）
- 中文字体：Noto Sans CJK（`brew install --cask font-noto-sans-cjk`）
- yt-dlp（`brew install yt-dlp`）

## 安装

```bash
uv sync
cp config.toml.example config.toml   # 填写 OSS bucket 等业务配置（样式预设见 presets.toml，默认含 default）
cp .env.example .env                 # 填写三个 API key（见下）
```

密钥通过环境变量提供（.env 或 export）：

| 变量 | 用途 |
| --- | --- |
| `DASHSCOPE_API_KEY` | 阿里云百炼（ASR），北京/新加坡地域 Key 不通用 |
| `DASHSCOPE_WORKSPACE_ID` | 百炼工作空间 ID（北京地域 endpoint 需要） |
| `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` | 阿里云 OSS（音频上传） |
| `DEEPSEEK_API_KEY` | DeepSeek（翻译） |

## 使用

```bash
uv run quicksrt all <youtube-url>        # 全链路
uv run quicksrt download <youtube-url>   # 或分步执行
uv run quicksrt extract
uv run quicksrt transcribe
uv run quicksrt translate
uv run quicksrt refine   # 显示层优化（拆句/标点/接缝/双语）
uv run quicksrt srt
uv run quicksrt burn
uv run quicksrt preview --res 1080p   # 纯色背景渲染单条字幕 PNG 预览（样式预览，默认第 1 条，--index 指定）
uv run quicksrt preview --res 1080p --background white  # 覆盖背景色（或 #202020 等 ffmpeg 颜色值）
uv run quicksrt preview --inline-image  # 同上，并在 iTerm2 终端内直接展示图片
uv run quicksrt status                   # 查看流水线状态
uv run quicksrt clean -y                 # 删除中间产物
```

分步命令默认操作最新的 work 目录，多视频时用 `-i <video_id>` 指定。`--force` 强制重跑单环节；中断后重跑同一命令会从断点继续（ASR 任务按 task_id 恢复轮询，翻译按批次缓存跳过）。

翻译环节使用结构化输出（`json_object` 模式 + pydantic 逐条校验），批次并行翻译（`[translate] max_concurrency`）。可通过 `[translate] context_template` 注入视频上下文（占位符取 meta.json 字段，如 `{title}` `{description}` `{uploader}` `{url}`，缺失渲染为空串；修改模板会触发重译）。

## 开发

```bash
uv sync                 # 安装依赖（含 dev 组的 pytest）
uv run pytest           # 运行全部单元测试（纯本地，不涉及外部系统）
```

测试覆盖：核心数据模型序列化（models）、翻译批次解析与重试/兜底逻辑（translate，HTTP 层 mock）、ASR 结果解析（transcribe）、refine 拆句/标点/时间分配、SRT 规范化与渲染、config 合并、util 工具、ASS 样式与语言模式生成（burn）、preview 分辨率解析/条目选取/命令构建。外部系统（OSS、DeepSeek/ASR HTTP 调用、ffmpeg、yt-dlp）不在测试范围。

提交信息遵循 Conventional Commits：`fix:` 修 bug、`feat:` 新功能、`refactor:` 重构（行为不变）、`docs:` 文档、`chore:` 杂项；scope 可选（如 `fix(cli):`）。描述小写开头、祈使句、不加句号。

## 产物结构

```
work/<video_id>/
  video.mp4          下载的视频
  audio.wav          16kHz 单声道（ASR 输入）
  meta.json          元数据与各环节状态
  asr_raw.json       阿里云原始识别结果
  segments_en.json   英文 segments（统一格式）
  segments_zh.json   中文 segments
  batches/tr_*.json  翻译批次缓存
  refined.json        refine 后双语条目（拆句/标点/接缝已处理）
  subs.srt / subs.ass 字幕中间产物
dist/<标题>.mp4           最终成品
  <标题>_preview_<分辨率>.png  预览 PNG（preview 命令）
```

## 配置要点（config.toml）

- `[asr]` 默认 `qwen3-asr-flash-filetrans`（异步文件转写，支持字级时间戳，最长 12 小时），源语言 `en`
- `[oss]` 音频上传到私有 bucket，生成 7 天预签名 URL 供 ASR 拉取，用完即弃
- `[refine]` 显示层优化：拆句（分句标点处、可配置最大长度）、去句号、填平微小间隔
- `[style]` 烧录样式：可引用 `presets.toml` 中的命名预设（`preset = "default"`），预设为基底、`[style]` 显式字段覆盖；样式项：Noto Sans CJK SC、白字黑描边、字号/边距按分辨率比例；语言模式 `mode`（`bilingual` 双语 / `mono` 单语）+ `primary_lang`（`zh`/`en`，主语言在上大字号、副语言在下小字号）；中文样式 `font_name`/`font_bold`/`font_italic`，英文独立样式 `en_font_name`/`en_bold`/`en_italic`/`en_font_ratio`（英文字号默认 60%）；字幕背景 `bg_enabled`/`bg_color`（ASS AABBGGRR，默认半透明黑 `&H80000000`）/`bg_padding_ratio`——全宽半透明矩形条，分层渲染，随字幕显示
- `[preview]` 预览背景色 `background`（ffmpeg color 源支持的颜色名或 `#RRGGBB`，默认 black）
- `[burn]` 按源编码器自动选 libx264/libx265/libsvtav1，CRF 质量模式，音频流 copy 不重编码

已知限制：硬烧录必然重编码，画质损失通过 CRF 18 + slow preset 控制；源视频为 HDR 时未做色域转换，输出按 yuv420p 处理。
