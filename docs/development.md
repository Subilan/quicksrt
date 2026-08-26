# 开发指南

## 环境

- Python >= 3.12
- uv（依赖管理，见 [README 环境要求](../README.md) 中的运行时依赖）

```bash
uv sync                 # 安装依赖（含 dev 组的 pytest）
uv run pytest           # 运行全部单元测试
```

运行时还依赖 ffmpeg（带 libass）、yt-dlp、fc-list（fontconfig，macOS/Linux 自带），这些在单元测试中会被 mock 或跳过，不参与测试。

## 测试

测试全部为纯本地单元测试，**不涉及任何外部系统**（OSS、DeepSeek/ASR HTTP 调用、ffmpeg 实际渲染、yt-dlp 下载都不在测试范围）。涉及外部调用的代码路径通过 mock 覆盖（如 translate 的 HTTP 层）。

### 测试文件与覆盖范围

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_models.py` | Segment/Word 数据模型序列化、时间字段校验与修正 |
| `tests/test_translate.py` | 翻译批次切分、结构化输出解析（json_object）、重试与兜底逻辑 |
| `tests/test_transcribe.py` | ASR 原始结果解析（毫秒→秒、异常时间修正） |
| `tests/test_refine.py` | 拆句（标点/空格分句、时间按字符比例分配）、去句号、接缝优化 |
| `tests/test_srt.py` | SRT 规范化（时长 clamp、去重叠、断行）与渲染 |
| `tests/test_config.py` | 配置加载、内置默认填充、presets 展开与继承（链式/循环检测）、显式键覆盖优先级 |
| `tests/test_util.py` | 时间戳格式化、meta 状态（step_done）、workdir 查找、颜色解析（CSS→ASS）、字体可用性检查与 fontconfig 模式解析 |
| `tests/test_burn.py` | ASS 时间戳/转义、样式块生成（颜色/粗体/斜体）、语言模式（bilingual/mono/primary_lang）、字体缺失回退、fontconfig 模式转 ASS 字体名、编码器选择 |
| `tests/test_preview.py` | 分辨率解析（预设/auto/非法）、条目选取（--index）、text-only 包围盒解析、命令构建 |

### 写测试的约定

- 复杂、系统化、不涉及外部系统、值得用测试描述特性的实现，必须配套测试（见 [AGENTS.md](../AGENTS.md)）
- 涉及外部系统的逻辑（如 burn 的 ffmpeg 实际渲染）不写集成测试，用纯函数拆分 + mock 覆盖
- 纯函数优先：把可测逻辑（编码器选择、时间分配、字体名解析）从 IO 代码中拆出来，便于直接测试

## 代码结构

入口 `quicksrt/cli.py` 用 typer 定义子命令，每个子命令对应 `quicksrt/steps/` 下一个模块的 `run(cfg, workdir, log, ...)`。模块职责见 [产物结构文档的仓库布局](project-structure.md#仓库布局)。

关键设计约定：

- **断点状态统一走 `meta.json` 的 `steps` 表**，每环节实现 `STEP = "xxx"`，用 `util.step_done(meta, STEP, **match)` 检查是否可跳过，`match` 里放关键参数（如 ASR 模型名）
- **日志统一用 `util.setup_logging(workdir)`**：终端着色 + 落盘 `work/<id>/quicksrt.log`；外部命令统一走 `util.run_cmd`（记录命令、失败抛 `RuntimeError` 并带 stderr）
- **配置合并**在 `config.py`：内置默认 < presets.toml 展开 < config.toml 显式键；新配置项务必同步更新 `DEFAULT_TOML` 与 `config.toml.example`

## 提交规范

遵循 Conventional Commits 1.0.0：`<type>[optional scope]: <description>`。

- type 必填：`fix` 修 bug / `feat` 新功能 / `refactor` 重构（行为不变）/ `docs` 文档 / `chore` 杂项（依赖、构建配置）
- scope 可选：如 `fix(cli):`、`feat(api):`
- description：小写开头、祈使句、末尾不加句号

示例：`fix: correct ass timestamp carry`、`feat(burn): support fontconfig :style= syntax in font names`。

## 调试

- `uv run quicksrt --no-color <子命令>`：禁用终端日志颜色（或设置 `QUICKSRT_NO_COLOR=1` / `NO_COLOR=1`）
- 日志默认 INFO 级（环节进度、外部命令、结果），子进程 stdout 只进 DEBUG 级；需要看 ffmpeg/yt-dlp 详细输出时，临时把 logger 调为 DEBUG（`util.setup_logging(workdir, verbose=True)`）
- 各环节产物与断点状态：`quicksrt status` 查看 `steps` 表；直接删除某产物文件即可触发该环节重跑（见[产物结构](project-structure.md#断点续跑机制)）
