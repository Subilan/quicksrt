# 产物结构

## 仓库布局

```
quicksrt/
├── README.md              用户手册（面向用户）
├── references/docs/      技术文档（本目录）；references/ 下另有外部参考资料
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
│       ├── refine.py      可选显示层打磨（就地改写 subs.srt）
│       ├── srt.py         基础 SRT 生成 / v2 解析写入
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
| `segments_<源语言码>.json` | transcribe | ASR segments（如 `segments_en.json`），统一格式（秒级时间戳），语言码取 `[asr] language` |
| `segments_<目标语言码>.json` | translate | 翻译 segments（如 `segments_zh.json`），语言码取 `[translate] target_lang`，与源语言按 id 一一对应 |
| `batches/tr_*.json` | translate | 翻译批次缓存（每批一个文件），支持细粒度断点续跑：重跑只翻译缺失批次 |
| `subs.srt` | srt | **v2 双语字幕（显示层唯一载体）**：SRT 骨架 + 每条 cue 的「主语言块 + 分隔行（`[srt] block_separator`，默认 `--`）+ 副语言块」，块内行数不限、无分隔行=只有主语言。srt 生成基础稿，refine 可选打磨并就地改写，burn/preview/人工编辑都消费它；refine 不再产出 JSON |
| `subs.ass` | burn | 按样式配置生成的 ASS（中间产物，调试用） |
| `preview.ass` / `preview_crop.ass` | preview | 预览用的临时 ASS |
| `.oss_upload/` | upload | OSS 分片上传断点（重传时跳过已上传分片） |
| `quicksrt.log` | 各环节 | 环节日志文件（终端输出同时落盘）。extract 及之后的环节写 `work/<id>/quicksrt.log`；download 的日志写在 `work/quicksrt.log`（此时视频目录尚未创建） |

### meta.json

各环节共享的状态中心，JSON 格式。核心字段：

| 字段 | 写入环节 | 说明 |
| --- | --- | --- |
| `video_id` | download | YouTube 视频 ID |
| `title` / `description` / `uploader` | download | 视频元信息（翻译 context_template 的占位符来源） |
| `url` | download | 原始链接 |
| `duration` | download | 视频时长（秒） |
| `audio_url` | upload | OSS 预签名 URL（ASR 拉取用） |
| `asr` | transcribe | `{model, language, provider}`，language 即源语言码，用于判断断点是否过期（换模型后重跑） |
| `translate` | translate | `{model, temperature, source_lang, target_lang, prompt_template, context_template}`，语言方向/模板变更后重译 |
| `refine` | refine | refine 参数快照，参数（含 `block_separator`）变更后自动重跑 |
| `srt` | srt | srt 参数快照（语言、折行、`block_separator`），参数变更后自动重建基础稿 |
| `subs` | srt/refine | 当前 subs.srt 的 `{owner: "srt"\|"refine", digest}` 指纹，用于覆写守卫（外部编辑不覆盖，--force 才覆盖） |
| `burn` | burn | 烧录配置快照 `{encoder, crf, preset, style, subs_digest}`；样式/参数/字幕内容（含人工编辑）变更后自动重烧 |
| `steps` | 各环节 | 环节状态表：`{"download": "done", "extract": "done", ...}`。键存在且值为 `"done"` 表示该环节完成；缺失或为 `null` 表示未完成/已重置 |

### 断点续跑机制

每个环节开始前检查 `steps[本环节] == "done"` 且关键参数未变（`util.step_done`），满足则跳过。参数校验按环节不同：

- transcribe：检查 `meta.asr.model` 与当前配置一致（换模型自动重转）
- translate：按 `batches/` 缓存，缺哪批翻哪批；source/target 语言、prompt/context 模板变更会触发重译
- srt / refine / burn：保存配置快照。配置变更自动重跑；burn 另记录 `subs.srt` 内容指纹（人工编辑后重跑即重烧）；srt/refine 覆写前先比对外部修改（指纹不符默认跳过，`--force` 才覆盖）
- 其余环节：只检查 `steps` 状态与产物文件是否存在

CLI `--force` 把对应环节的 `steps` 重置为 `null` 强制重跑（分步执行，无 `all` 命令）。

## dist/

成品输出目录。

| 文件 | 产出命令 | 说明 |
| --- | --- | --- |
| `<标题>.mp4` | burn | 烧录完成视频（标题清洗：非法字符替换为 `_`，截断 80 字符） |
| `<标题>_preview_<分辨率>.png` | preview | 普通预览：纯色背景 + 单条字幕，分辨率 = 指定值或源视频（如 `<标题>_preview_1080p.png`）；多预设（--preset a,b / --all-preset）时为 `<标题>_preview_<预设>_<分辨率>.png` |
| `<标题>_preview_crop.png` | preview --crop | 文字裁剪图：紧贴文字包围盒、保留纯色背景的 PNG（多预设时为 `<标题>_preview_<预设>_crop.png`） |
| `example-<示例>_preview_<分辨率>.png` | preview --example | 内置示例文本预览（lorem/glass/fox，默认 lorem；不依赖 work 数据，auto 回退 1080p） |

## 中间产物生命周期

- 各环节产物只保留 `video.mp4`、`audio.wav`、`meta.json`、各 segments、`subs.srt`（+ `subs.srt.bak.*` 备份）；`asr_raw.json`、`asr_task.json`、`batches/`、`.oss_upload/` 属可再生的断点缓存，删除不丢功能（仅下次重跑时需重做对应环节）。全链路分步执行（无 `all` 命令）
- `quicksrt clean -y` 删除整个 `work/<video_id>/` 目录（全部中间产物）
- 各环节产物可直接删除以触发该环节重跑（配合断点机制），无需手动改 meta.json
