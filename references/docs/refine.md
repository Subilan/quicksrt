# subs.srt 与 refine

`subs.srt` 是 quicksrt 显示层的**唯一载体**：srt 生成、refine 打磨、burn/preview 渲染、人工编辑，
全部指向同一个文件。它是 quicksrt 的双语规范 SRT（下称 v2），不是 JSON 派生物——人工改完即最终结果，
burn 永远消费文件当前内容。

```
segments_<primary>.json ──┐
segments_<secondary>.json ─┴─> srt ──> subs.srt（v2 双语基础稿）
                                        │（可选）refine：就地改写
                                        │（可选）人工直接编辑（增删条/改时/改文）
                                        v
                              burn / preview：每次现读现解析 subs.srt
```

## v2 格式：主/副语言块 + 分隔行

SRT 骨架（序号可有可无、`HH:MM:SS,mmm` 时间行、cue 间空行），一条 cue 的文本为：

```
1
00:00:01,000 --> 00:00:04,000
你好啊，欢迎来到传送门速通的世界。      <- 主语言块（可多行）
--
Hello there, welcome to Portal speedrunning.   <- 副语言块（可多行）
```

规则：

- **主语言块在上、副语言块在下，中间是单独一行的分隔符**（`[srt] block_separator`，默认 `--`）。
- **无分隔行 = 该 cue 只有主语言**（mono 内容 / 双语里某条删掉了副语言 → 渲染成仅主行）。
- **块内行数不限**：块内换行只表示折行，不再承担"主语言/副语言分界"职责——
  这是对旧版"用 `\n` 分隔主副语言"的根本改进：人工把主语言长句折成两行、或删掉副语言，
  角色判定都不会错位（旧约定下最后一行是副语言，一折行就静默错位）。
- 机器书写恒为「主一行 + 分隔行 + 副一行」，往返幂等；解析器容错
  （BOM/CRLF、HTML 标签、任意毫秒精度、无序号），结构错误报错带块位置，
  时间倒置自动交换、相邻重叠/多条分隔行/空块等给 warning 不中断。
- 分隔符只在整行等于它时生效；若你的字幕正文恰会出现整行等于分隔符的内容，
  把它换成不冲突的字符串即可（如 `==`）。**已生成的文件按生成时记录的分隔符解析/书写**
  （记录在 meta 的 srt/refine 快照里），中途改配置不会把旧文件误读；要让旧文件改用新
  分隔符，先 `quicksrt srt --force` 重建基础稿。

## 各环节职责

### srt：生成基础稿（双语不依赖 refine）

从 `segments_<primary_lang>.json` 与 `segments_<secondary_lang>.json`（翻译保证按 id 一一对应、
时间一致）配对生成。每条 cue 时间以主语言为准做时长 clamp 与去重叠；主/副文本各自**无损折行**
到 `[srt] max_lines` 行（折行只插入换行不删字符）。只有单语言 segments 时输出主语言 cue。

- `srt` 把产物写进 `meta.json` 的 `subs = {owner: "srt", digest}`，并把 `[srt]` 参数快照存为 `meta.srt`；
  参数（含 `block_separator`）变化自动重新生成。
- 覆写守卫：`subs.srt` 已被 refine 改写或外部（人工）编辑过时，再次 `srt` 默认跳过，
  `--force` 才覆盖（覆盖前备份为 `subs.srt.bak.pre-srt-*`）。

### refine：可选的显示层打磨

`refine` 读取 subs.srt，逐 cue 做三项优化后**写回同一个文件**（不再产出任何 JSON 中间件）：

1. 拆句：主语言文本超过 `[refine] max_chars` 时只在分句标点处拆成多条 cue，
   时间按字符数比例从原句时间分配；副语言按相同比例同步拆，双语条目一一对应。
2. 去标点：去掉拆句主语言文本末尾的句号/逗号等（`[refine] strip_end_punct`，集合按语言规则表）。
3. 接缝：相邻 cue 间隔小于 `[refine] min_gap` 时前一条延伸至后一条开始，消除闪烁。

拆句规则内置 zh/ja/en 语言表（分句标点/句尾去除/行内断行字符，未知语言走 Unicode 通用规则），
按显示主语言（`[style] primary_lang`）取表，`[refine]` 的 `split_punct`/`strip_punct`/`break_after`
可覆盖。refine 先把块内既有折行**拍平还原**再重新排版，因此结果与基础稿是否折行无关（幂等）。

跳过 refine 直接 burn 也可：双语、断点、preview 全不受影响，只是少了这三项打磨。

守卫与断点：refine 把 `meta.refine` 存为参数快照（含 `block_separator`），参数未变且 `steps.refine`
为 done 时跳过；文件被外部（人工）编辑过则默认跳过并提示，`--force` 才覆盖（先备份为
`subs.srt.bak.pre-auto-*`）。refine 完成后写 `subs = {owner: "refine", digest}` 并重置 burn 断点。

### burn / preview：永远读 subs.srt 当前内容

burn 解析 subs.srt → 主/副块 → 按 `[style] mode`（bilingual/mono）与角色样式渲染 ASS 烧录；
preview 同源渲染单条。两者都没有覆写守卫——文件当前是什么就烧什么，人工编辑后直接重跑即生效。
burn 的断点快照包含字幕文件 digest：字幕内容（含人工编辑）变化会自动重烧，不会因
`steps.burn` 已 done 而跳过。

## 人工精调工作流

机器跑完（可含 refine）后直接编辑 `subs.srt`：改措辞、改时间、拆/并/增/删条都允许；
想在某条只显示主语言就把副块连同分隔行删掉。改完重新 `quicksrt burn`（`preview --index N` 可先预览某条），
**无需任何导入/导回动作**——你编辑的文件本身就是 burn 的输入。

注意两件事：

- 若你加了一条全新的 cue，记得按 v2 结构写（主块 + 分隔行 + 副块，无副语言就只写主块）。
- `srt`/`refine` 重跑会重建/改写该文件并**覆盖人工修改**：它们检测到文件与记录的指纹不一致时
  会默认跳过（日志提示），确需覆盖时加 `--force`（先备份）。不同视频（work 目录）互不影响。

## 断点与重跑

- 每个环节开始前检查 `meta.json` 的 `steps.<环节> == "done"` 且参数快照未变，满足则跳过；
  参数（srt/refine/burn 各自的配置、语言、分隔符）变化后自动失效重跑。
- `--force` 强制重跑单环节（含清掉对应断点）。
- 旧的 `refined.json` / `refine --manual` / `all` 链路已移除：显示层不再有 JSON 数据源，
  `subs.srt` 就是唯一事实。
