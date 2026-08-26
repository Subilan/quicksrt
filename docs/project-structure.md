# 产物结构

## 仓库布局

```
quicksrt/
├── README.md              用户手册（面向用户）
├── docs/                  技术文档（本目录）
├── config.toml            业务配置（从 config.toml.example 复制，勿提交 git）
├── config.toml.example    配置示例
├── presets.toml           样式预设库
├── .env                   密钥（从 .env.example 复制，勿提交 git）
├── .env.example           密钥示例
├── pyproject.toml         项目定义（uv/pytest）
├── quicksrt/              源码包
│   ├── cli.py             typer CLI 入口（子命令定义）
│   ├── config.py          配置加载：DEFAULT_TOML 内置默认、presets 展开、合并逻辑
│   ├── models.py          Segment/Word 数据模型（pydantic）
│   ├── util.py            通用工具（日志/子进程/ffprobe/meta/颜色/字体名解析）
│   └── steps/             每个环节一个模块
│       ├── download.py    yt-dlp 下载
│       ├── extract.py     ffmpeg 提取 16k 音频
│       ├── upload.py      OSS 上传 + 预签名 URL
│       ├── transcribe.py  阿里云 ASR 转写
│       ├── translate.py   DeepSeek 翻译
│       ├── refine.py      显示层后处理
│       ├── srt.py         SRT 生成
│       ├── burn.py        ASS 生成 + ffmpeg 烧录
│       └── preview.py     字幕 PNG 预览
├── tests/                 单元测试
├── work/                  工作目录（每个视频一个子目录）
└── dist/                  成品输出（烧录视频 + 预览 PNG）
```

## work/<video_id>/

每个视频一个目录，存放该视频全部中间产物与状态。`<video_id>` 是 YouTube 视频 ID，由 download 环节自动获取。

| 文件/目录 | 产出环节 | 说明 |
| --- | --- | --- |
| `video.mp4` | download | 下载的视频（yt-dlp 最佳视频+音频流自动合并） |
| `audio.wav` | extract | 16kHz 单声道 PCM WAV，ASR 输入 |
| `meta.json` | 各环节 | **元数据与环节状态中心**，见下文 |
| `asr_task.json` | transcribe | 转写任务断点：`{task_id, model}`，提交任务后落盘；轮询完成、结果下载后删除 |
| `asr_raw.json` | transcribe | 阿里云返回的原始识别结果（逐句 + 字级时间戳，毫秒） |
| `segments_en.json` | transcribe | 英文 segments，统一格式（秒级时间戳），下游唯一英文数据源 |
| `segments_zh.json` | translate | 中文 segments，与英文按 id 一一对应 |
| `batches/tr_*.json` | translate | 翻译批次缓存（每批一个文件），支持细粒度断点续跑：重跑只翻译缺失批次 |
| `refined.json` | refine | 双语条目：`[{id, src_id, start, end, zh, en}]`，拆句/标点/接缝已处理；`zh`/`en` 中可含 `\n` 表示行内换行。**srt（双语）与 burn/preview 的实际数据源** |
| `subs.srt` | srt | 规范化 SRT（refined.json 存在时输出双语：中文在上、英文在下） |
| `subs.ass` | burn | 按样式配置生成的 ASS（中间产物，调试用） |
| `preview.ass` / `preview_text.ass` | preview | 预览用的临时 ASS |
| `.oss_upload/` | upload | OSS 分片上传断点（重传时跳过已上传分片） |
| `quicksrt.log` | 各环节 | 环节日志文件（终端输出同时落盘）。extract 及之后的环节写 `work/<id>/quicksrt.log`；download / all 的日志写在 `work/quicksrt.log`（此时视频目录尚未创建） |

### meta.json

各环节共享的状态中心，JSON 格式。核心字段：

| 字段 | 写入环节 | 说明 |
| --- | --- | --- |
| `video_id` | download | YouTube 视频 ID |
| `title` / `description` / `uploader` | download | 视频元信息（翻译 context_template 的占位符来源） |
| `url` | download | 原始链接 |
| `duration` | download | 视频时长（秒） |
| `audio_url` | upload | OSS 预签名 URL（ASR 拉取用） |
| `asr` | transcribe | `{model, language, provider}`，用于判断断点是否过期（换模型后重跑） |
| `translate` | translate | `{model, temperature, context_template}`，配置变更后重译 |
| `refine` | refine | refine 配置快照，配置变更后自动重跑 |
| `burn` | burn | 烧录配置快照 `{encoder, crf, preset, style}`，样式/参数变更后自动重烧 |
| `steps` | 各环节 | 环节状态表：`{"download": "done", "extract": "done", ...}`。键存在且值为 `"done"` 表示该环节完成；缺失或为 `null` 表示未完成/已重置 |

### 断点续跑机制

每个环节开始前检查 `steps[本环节] == "done"` 且关键参数未变（`util.step_done`），满足则跳过。参数校验按环节不同：

- transcribe：检查 `meta.asr.model` 与当前配置一致（换模型自动重转）
- translate：按 `batches/` 缓存，缺哪批翻哪批；context_template 变更会触发重译
- refine / burn：保存配置快照，配置变更自动重跑
- 其余环节：只检查 `steps` 状态与产物文件是否存在

CLI `--force` 把对应环节的 `steps` 重置为 `null` 强制重跑；`all --force` 重置全部环节。

## dist/

成品输出目录。

| 文件 | 产出命令 | 说明 |
| --- | --- | --- |
| `<标题>.mp4` | burn | 烧录完成视频（标题清洗：非法字符替换为 `_`，截断 80 字符） |
| `<标题>_preview_<分辨率>.png` | preview | 普通预览：纯色背景 + 单条字幕，分辨率 = 指定值或源视频（如 `<标题>_preview_1080p.png`、`_preview_1920x1080.png`） |
| `<标题>_preview_text.png` | preview --text-only | 文字裁剪图：紧贴文字包围盒、保留纯色背景的 PNG |

## 中间产物生命周期

- `all` 全链路跑完只保留 `video.mp4`、`audio.wav`、`meta.json`、各 segments、`refined.json`、`subs.srt`；`asr_raw.json`、`asr_task.json`、`batches/`、`.oss_upload/` 属可再生的断点缓存，删除不丢功能（仅下次重跑时需重做对应环节）
- `quicksrt clean -y` 删除整个 `work/<video_id>/` 目录（全部中间产物）
- 各环节产物可直接删除以触发该环节重跑（配合断点机制），无需手动改 meta.json
