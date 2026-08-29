# quicksrt

YouTube 视频 → 硬字幕烧录的一站式管线（语言无关：源/目标/主/副语言均可配置）。每一环都是独立命令，产物落盘支持断点续跑。

```
下载(yt-dlp) → 提取音频(ffmpeg) → 上传OSS(预签名URL) → ASR转写(阿里云百炼) → 翻译(DeepSeek) → refine(拆句/标点/接缝/双语) → 生成SRT → 烧录(libass)
```

## 语言模型

链路用「语言码」标识语言（BCP-47 风格，如 en/zh/ja/ko）、用「角色」标识职责：

- `source`：源语言（视频原始语言）＝ `[asr] language`
- `target`：翻译目标语言 ＝ `[translate] target_lang`
- `primary`/`secondary`：显示主/副语言（主语言在上、大字号）＝ `[style] primary_lang`/`secondary_lang`，缺省分别取 target/source

样式（`[style]`/`presets.toml`）只描述 primary/secondary 两个显示角色的视觉属性，与语言无关——同一套样式可套用任意语言组合。翻译提示词通过 `[translate] prompt_template` 完全可自定义（内置模板按语言名表渲染）。拆句/标点规则内置 zh/ja/en 语言表，未知语言走通用规则，`[refine]` 可覆盖。

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
uv run quicksrt preview --display  # 同上，并在 iTerm2 终端内直接展示图片（渲染到临时目录、不产生文件；终端不兼容则直接退出）
uv run quicksrt preview --crop         # 只渲染文字本身：输出紧贴文字范围的 PNG（--res/--video-id/--background 无效）
uv run quicksrt preview --example lorem # 用内置示例文本预览（lorem/glass/fox，默认 lorem），不依赖已有 work 数据
uv run quicksrt preview --example-primary "你好" --example-secondary "Hello"  # 手动构造示例文本（与 --example 互斥；只给一方时另一方用它兜底，语言码键取 [style] 配置）
uv run quicksrt preview --preset plex,plex_yellow  # 逗号分隔渲染多个样式预设
uv run quicksrt preview --all-preset --display  # 批量渲染 presets.toml 全部预设（与 --preset 互斥），终端内逐个展示
uv run quicksrt status                   # 查看流水线状态
uv run quicksrt clean -y                 # 删除中间产物
```

分步命令默认操作最新的 work 目录，多视频时用 `-i <video_id>` 指定。`--force` 强制重跑单环节；中断后重跑同一命令会从断点继续（ASR 任务按 task_id 恢复轮询，翻译按批次缓存跳过）。

翻译环节使用结构化输出（`json_object` 模式 + pydantic 逐条校验），批次并行翻译（`[translate] max_concurrency`）。语言方向由 `[translate] source_lang`（缺省取 `[asr] language`）→ `target_lang` 决定，任意语言组合均可。可通过 `[translate] context_template` 注入视频上下文（占位符取 meta.json 字段，如 `{title}` `{description}` `{uploader}` `{url}`，缺失渲染为空串；修改模板会触发重译）。

已知限制：硬烧录必然重编码，画质损失通过 CRF 18 + slow preset 控制；源视频为 HDR 时未做色域转换，输出按 yuv420p 处理。

## 文档

技术细节见 `references/docs/`：

- [开发指南（测试/代码结构/提交规范）](references/docs/development.md)
- [产物结构（work/ 与 dist/ 每个文件说明）](references/docs/project-structure.md)
- [配置文件详解（config.toml 全部字段 / presets.toml / .env）](references/docs/configuration.md)
