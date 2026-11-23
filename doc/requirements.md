# 需求文档：Jellyfin 影视入库助手（jf-ingest）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v0.4（阶段一只读预览 + 阶段二按确认表执行，均已实现） |
| 编写日期 | 2026-11-18（v0.2/v0.3：2026-11-19；v0.4：2026-11-23） |
| 程序名称（暂定） | `jf-ingest` |
| 运行平台 | 仅 Linux（开发调试可在 Windows 上进行，见 §4） |
| Python 版本 | **目标机 3.10.12，纯标准库**（开发机 3.13 仅用于跑测试，代码保持 3.10+ 兼容） |
| 状态 | **阶段一（只读预览）+ 阶段二（apply 执行）均已实现**；预览已跑过真实数据；执行未在真实媒体上运行 |

---

## 1. 背景

Jellyfin 通过「库目录（Library）+ 命名规范」来识别影视内容。目前的流程是：

1. 有人手工把下载/拷贝好的影视文件放到 `/data/upload`；
2. 传输完成后，人工执行脚本；
3. 脚本需要把这些文件整理成 Jellyfin 能正确识别、能自动刮削元数据的结构，落到：
   - 电影库：`/data/jellyfin/movies`
   - 剧集库：`/data/jellyfin/tvshows`

当前整理工作是手工的，容易出错：目录漏建、季/集编号写错、字幕文件与视频文件名不一致导致字幕不生效、扩展名被误改导致无法播放或重新编码等。

## 2. 目标与非目标

### 2.1 目标

| 编号 | 目标 |
| --- | --- |
| G-1 | 一条命令把 `/data/upload` 中的影视文件整理进对应的 Jellyfin 库目录 |
| G-2 | 生成的目录/文件名完全符合 Jellyfin 官方命名规范，保证能被识别和刮削 |
| G-3 | 视频文件扩展名、容器格式、编码一律不改动（mkv 还是 mkv，avi 还是 avi） |
| G-4 | 同目录下的外挂字幕随视频一起改名，字幕主名与视频主名一致（不映射语言标签，见 FR-05） |
| G-5 | 支持「预览（dry-run）」：执行前能看到「哪个源文件会变成哪个目标路径」，并可导出报告留档 |
| G-6 | 关键过程有日志（扫描、识别、决策原因、移动/重命名结果、失败原因） |
| G-7 | 支持同一电影多分辨率/多版本并存（`片名 (2023) - 1080p.mkv`） |
| G-8 | 花絮、预告、主题曲、封面图等附加文件一并整理，能被 Jellyfin 识别 |
| G-9 | 执行完成后可调用 Jellyfin API 触发库刷新，并设置 `jellyfin:jellyfin` 属主 |

### 2.2 非目标（本期不做）

- 不做转码、不做容器重封装、不修正损坏文件；
- 不做下载、不做上传完成检测服务（由外部流程保证「传输完成」后手工执行，决策见 §14）；
- 不做 nfo 生成、不做元数据手工编辑；
- 本期不实现 TMDb/TVDB 联网补年份（只预留接口与配置项，见 FR-16）；
- 不做 Jellyfin 库内已有文件的批量重命名（本期只处理 `/data/upload` 的内容）；
- 不做定时任务 / inotify 监听（本期手动执行，见 FR-15）；
- 不主动删除源文件（默认 `move`，源文件移动后自然消失，失败时保留并告警）。

## 3. 名词约定

| 名词 | 含义 |
| --- | --- |
| 源目录 | `/data/upload`，人工投放影视文件的地方 |
| 电影库 | `/data/jellyfin/movies` |
| 剧集库 | `/data/jellyfin/tvshows` |
| 条目（Item） | 一次被处理的最小单元：一个电影，或一集剧集 |
| 预览 / dry-run | 只计算并输出「计划做什么」，不修改磁盘 |
| apply | 真正执行移动/改名 |
| 主名（stem） | 文件名去掉最后一个扩展名的部分，如 `电影 (2020)` |
| 置信度 | 程序对识别结果的把握程度（high / medium / low） |

## 4. 运行环境

> 以下为 **2026-11 在目标机（luna-server）上实测**的结论，不是假设。

| 项 | 要求 |
| --- | --- |
| 操作系统 | Linux（目标机 Ubuntu，内核 5.x/6.x） |
| Python | **3.10.12**（系统自带；没有 3.12/3.13，**没有 pip**，`venv` 也建不出来） |
| 运行时依赖 | **零第三方依赖，纯标准库**（不能 `pip install`，所以配置格式用 **JSON**，不用 TOML） |
| 权限 | 源目录可读、库目录可写即可；**库目录已有默认 ACL `user:jellyfin:rwx`，无需 chown、无需 root** |
| 磁盘 | `/data` 已用 99%（剩 9.6 GB）→ **只能 move，不能 copy**；也不要解压大压缩包 |
| 文件系统 | 源目录与库目录同一分区（`/dev/sdb1`），`rename()` 秒级完成 |
| 时区/编码 | 文件名包含中文/日文/空格，按 UTF-8 处理，NFC 规范化 |
| 可用外部命令 | `unzip` 有；`7z` / `ffprobe` / `mediainfo` / `tree` **都没有** |

设计上要求：核心逻辑与平台无关，Windows 开发机用临时目录跑单元测试。

### 4.1 交付范围

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| **阶段一** | **只读预览**：扫描、识别、生成报告（MD / JSON / 确认表 TSV），**不移动、不改名、不 chown** | 已实现（v0.3），真实数据已跑 |
| **阶段二** | 按确认表执行：move（可选 copy/hardlink）、字幕改名、花絮归类、**写 `jf-ingest.md` 入库记录（FR-17）**、Jellyfin 刷新、apply 报告 | 已实现（v0.4），仅测试目录验证；未在真实媒体上执行 |

> `apply` 只执行预览报告 §5.3「能够自动处理」的条目；§3 / §5.1 / §5.2 一律跳过。
> `plan.tsv` 用来选条目；逐文件源→目标映射取自同时间戳的 `preview.json`。

## 5. 输入 / 输出

### 5.1 输入（可能的投放形态）

程序必须能处理以下形态（`/data/upload` 下）：

```text
# A. 单文件电影
/data/upload/流浪地球2.2023.1080p.BluRay.x264.mkv
/data/upload/流浪地球2.2023.1080p.BluRay.x264.chs.srt

# B. 文件夹电影
/data/upload/Oppenheimer (2023)/Oppenheimer.2023.2160p.mkv
/data/upload/Oppenheimer (2023)/Oppenheimer.2023.2160p.eng.srt

# C. 剧集（带季目录）
/data/upload/三体/Season 1/三体.S01E01.mkv
/data/upload/三体/Season 1/三体.S01E01.chs.srt
/data/upload/三体/Season 1/三体.S01E02.mkv

# D. 剧集（无季目录，季集信息只在文件名里）
/data/upload/Shogun/Shogun.S01E03.1080p.WEB-DL.mkv

# E. 剧集（中文集数写法）
/data/upload/漫长的季节/漫长的季节 第03集.mkv

# F. 同一电影多版本（决策 4）
/data/upload/流浪地球2.2023.2160p.mkv

# G. 花絮/封面/主题曲（决策 5）
/data/upload/流浪地球2.2023.1080p/extras/幕后花絮.mkv
/data/upload/流浪地球2.2023.1080p/cover.jpg
/data/upload/三体/theme.mp3

# H. 待忽略的临时文件
/data/upload/xxx.mkv.part
/data/upload/xxx.mkv.!qB
/data/upload/.DS_Store
```

### 5.2 输出（目标结构）

```text
/data/jellyfin/movies/
├── 流浪地球2 (2023)/
│   ├── 流浪地球2 (2023).mkv
│   ├── 流浪地球2 (2023).srt              # 单份字幕：与视频完全同名
│   ├── 流浪地球2 (2023) - 2160p.mkv      # 多版本（决策 4）
│   ├── jf-ingest.md                      # 入库记录（决策 24 / FR-17）
│   ├── extras/
│   │   └── 幕后花絮.mkv
│   └── cover.jpg
└── Oppenheimer (2023)/
    ├── Oppenheimer (2023).mkv
    ├── Oppenheimer (2023).srt
    └── jf-ingest.md

/data/jellyfin/tvshows/
└── 三体 (2023)/
    ├── jf-ingest.md                      # 入库记录：写在「剧名文件夹」，不写进 Season 目录
    ├── theme.mp3
    ├── Season 01/
    │   ├── 三体 (2023) S01E01.mkv
    │   ├── 三体 (2023) S01E01.srt
    │   └── 三体 (2023) S01E02 科学边界.mkv   # 保留集标题（决策 1）
    └── Season 02/
        └── 三体 (2023) S02E01.mkv
```

> 说明：`<剧名> S01E01.<ext>` 中的 `<剧名>` 前缀是官方示例的写法，便于人工阅读；Jellyfin 识别时主要依赖 `SxxExx`。前缀可选，但本项目默认生成，保证同库内不混淆。
>
> `jf-ingest.md` 是**普通文本文件**，记录这个文件夹「原来长什么样、哪些文件改过名」。它不是视频、不是字幕、也不是歌词，Jellyfin 会直接忽略，不会出现在海报墙或播放列表里。详见 FR-17。

## 6. Jellyfin 命名规范核对（含官方依据）

> 依据：Jellyfin 官方文档 Movies / TV Shows 两页（见 §15），核对日期 2026-11-18。

### 6.1 电影

官方规则（Movies 文档「Organization / Naming」）：

1. 每部电影一个独立文件夹，文件夹名格式：

   ```text
   Movie Name (year) [metadata provider id]
   ```

   `year` 与 `metadata provider id` **可选**，但能显著提高匹配准确度。

2. 文件夹内的视频文件 **应与文件夹同名**：

   ```text
   Movies/Best_Movie_Ever (2019)/Best_Movie_Ever (2019).mp4
   ```

   「should have the same name as the folder」，可再加版本标签。

3. 同一文件夹内如果存在多个视频，Jellyfin 会按「文件名必须以文件夹主名开头 + 分隔符 + 版本标签」当作**同一部电影的多版本**。所以电影文件夹里只能放这一部电影的（多版本）视频，不能混放别的片。

4. 以下字符会出问题，不能出现在文件名中：`< > : " / \ | ? *`。

**结论（对你的说法的核对）**：

- ✅ 「电影要有一个文件夹，文件夹下放视频」——正确，也是官方推荐做法。
- ✅ 「视频文件与文件夹同名」——正确，官方明确建议同名；且多版本场景下必须严格同名开头。
- ⚠️ 补充：「同名」不是硬性识别条件（文件夹里只有一个视频时，Jellyfin 也能识别），但为了稳定，程序**一律生成同名**。
- ⚠️ 补充：文件夹名带**发行年份** `(2023)` 能降低「同名翻拍片刮错」的概率。但年份**不是 Jellyfin 的硬性要求**，官方也标注 `year` 为可选；源文件里没有年份时，程序不臆造、不写空括号，处理方式见 FR-03「年份缺失」与 FR-04。

### 6.2 剧集

官方规则（TV Shows 文档「Organization / Naming」）：

```text
Shows
└── Series Name A (2010)          <- 剧集文件夹：Series Name (year)
    ├── Season 00                 <- 特典/花絮
    ├── Season 01                 <- 季文件夹：Season + 两位序号
    │   ├── Series Name A S01E01-E02.mkv
    │   ├── Series Name A S01E03.mkv
    │   └── Series Name A S01E04.mkv
    └── Season 02
        └── Series Name A S02E01.mkv
```

1. 季文件夹名必须是 `Season *` 形式，**不能简写成 `S01` / `SE01`**；建议序号补零（`Season 5` → `Season 05`）。
2. **不要**把剧集视频直接混放在剧集文件夹根下（必须放进季文件夹）。
3. 剧集文件名必须能解析出季集，官方示例统一为 `SxxExx`：

   ```text
   Series Name A (2024) S01E01 episode name.mp4
   ```

   同一文件包含多集时写作 `S01E01-E02`；同一集多版本时保持相同 `SxxExx`，后面加 `- 1080p` 之类标签。
4. 特典/SP 放 `Season 00`（`S00E01`…）。
5. 剧集文件夹名格式同样是 `Series Name (year) [metadata provider id]`，年份可选但建议保留。

**结论（对你的说法的核对）**：

- ✅ 「剧集 = 剧名文件夹 / Season 01 / Season 02 …」——正确。
- ✅ 「文件名必须含 SXXEYY，季号要与所在季文件夹一致，集号两位」——正确。程序生成规范为 `S01E02`（`S`、`E` 大写，季号/集号两位补零；超过 99 集时按实际位数）。
- ⚠️ 补充：季文件夹必须写成 `Season 01`，不能写成 `S01`；`Season 1` 也建议统一成 `Season 01`。
- ⚠️ 补充：文件名里的季号与所在 `Season 0X` 文件夹**必须一致**，否则 Jellyfin 以文件名为准，会出现「季错乱」。
- ⚠️ 补充：剧集文件名的 `<剧名> S01E01` 前缀不是必须的，但建议加上，看起来更清晰，也便于排查。

### 6.3 字幕（外挂字幕）

官方规则（External Subtitles and Audio Tracks）：

- 外挂字幕必须与视频**同目录**，且以视频文件名为基础，追加可选的「标签/语言」段：

  ```text
  Movies/Film (1986)/
  ├── Film.mkv
  ├── Film.default.srt            <- 无语言标签，可作默认字幕
  ├── Film.default.en.forced.ass
  ├── Film.en.sdh.srt
  └── Film.English Commentary.en.mp3
  ```

- 标签段用 `.` 分隔，可以是：
  - 语言代码（如 `en`、`zh`、`zh-CN`、`ja`）；
  - 特殊标志：`default`、`forced`/`foreign`、`sdh`/`cc`/`hi`；
  - 其他任意文本会被当作「字幕轨标题」（不会报错，但语言识别不生效，影响客户端自动选字幕）。

**结论**：

- ✅ 「字幕文件要跟随视频文件名一起改名」——正确，这是关键需求。
- ⚠️ 补充：字幕不仅要「同名」，语言写规范也有好处。`.chs` / `.sc` / `.cn` / `中字` 这类国内常见写法不是标准语言码，Jellyfin 只会当成「字幕轨标题」。**本项目的决定是「不做映射、字幕名与视频一致」（见 FR-05 与 §14 决策 12）**；注意这不等于字幕不可用，详见下面的澄清。
- ⚠️ 注意：`VobSub` 字幕是 `.idx` + `.sub` 成对文件，两个文件必须一起改名，否则字幕加载失败。
- ⚠️ 源码级确认（2026-11，`MediaInfoResolver.GetExternalFiles` / `ExternalPathParser` / `NamingOptions`）：
  - 只扫描**视频所在目录**，**不递归子目录**——字幕放在 `subs/` 子目录里等于没有；
  - 分隔符只有 `.`（`MediaFlagDelimiters = ['.']`）：`片名 (2023).sc.ass` 有效，`片名 (2023)_sc.ass` / `片名 (2023) - sc.ass` **无效**；
  - 支持的扩展名：`.ass .mks .sami .smi .srt .ssa .sub .sup .vtt`；`.idx` 走 VobSub 配对；**`.ttml` / `.dfxp` 不在支持列表**（需转成 `.srt` 或放弃）。

**澄清：`.chs` 字幕到底还能不能用？**

能。要区分两件事：

| 能力 | 是否正常 | 说明 |
| --- | --- | --- |
| 字幕文件被 Jellyfin 发现并挂到视频上 | ✅ 正常 | 只要**同目录 + 主名与视频一致**（`Movie.mkv` ↔ `Movie.chs.srt`），就一定被识别为这部电影/这集的字幕，可以正常播放、手动切换 |
| 语言标签被正确识别 | ❌ 不生效 | Jellyfin 只认 ISO 639 语言码（`zh`、`zh-CN`、`en`、`ja`…）和特定标志（`default`/`forced`/`sdh`）；`chs`、`sc`、`cn`、`中字` 这类非标准写法会被当作「字幕轨标题」的普通文本（注：`chi`、`zho` 是合法 ISO 639-2 中文代码，是能识别的） |

不生效的实际后果：

1. 字幕语言显示为「Unknown」（或直接显示 `chs` 这个标题），而不是「中文」；
2. 客户端里「优先播放中文字幕 / 按语言自动选字幕」这类偏好失效——多字幕时可能默认不选、或选了英文字幕；
3. 不影响播放本身，手动选一下就能用。

**本项目的决定（评审确认）**：字幕**不做语言标签映射**，目标名尽量与视频名一致：

- 同一视频只有 1 份字幕 → 目标字幕名 = **视频主名 + 原扩展名**（语言类标签去掉）；
- 例外：`forced` / `sdh` / `default` 属于播放行为标志，保留不删（`片名 (2023).forced.srt`）；
- 同一视频有 ≥2 份字幕（如简繁各一份）→ 无法都叫同一个名字，此时**保留原始标签段**（原样，不映射），如 `片名 (2023).chs.srt` / `片名 (2023).cht.srt`，避免互相覆盖；
- 该行为可通过配置 `[subtitle] name_mode = "same_as_video" | "keep_tags"` 切换（默认 `same_as_video`）。

代价（已在 §14 记录）：`.chs` 这类标签仍然不被识别为语言，多字幕时客户端「按语言自动选字幕」可能不准；单字幕时不带标签，Jellyfin 也会正常加载，只是语言显示为未知。

另一个更常见的坑（比 `.chs` 更容易出问题）：**字幕主名必须与视频主名一致**。Jellyfin 源码（`MediaInfoResolver.GetExternalFiles`）的匹配逻辑是：把字幕文件名去掉扩展名后，必须以「视频主名」为前缀，且紧随其后的字符必须是 `.`、`-`、`_` 这类分隔符，否则直接不配对。所以视频叫 `Movie.2023.1080p.BluRay.mkv`、字幕却叫 `Movie.srt`，Jellyfin 是配不上的。本程序会把视频和字幕改成同一个主名，天然避免这个问题。


### 6.4 其它相关规则（程序需考虑）

| 规则 | 影响 |
| --- | --- |
| 保留字符 `< > : " / \ \| ? *` | 生成目标名时需替换/删除，否则 Jellyfin 刮削异常 |
| 多版本（1080p/2160p/Directors Cut） | 文件名必须以文件夹主名严格开头，否则会被当成两部电影（实现见 FR-13） |
| 多碟 / 多段（`-cd1`、`-part2`） | 同一部电影拆成多个文件时的合法写法（实现见 FR-13） |
| 花絮 / 预告（`extras/`、`trailers/`、`*-trailer`） | 本期支持，见 FR-14 |
| 主题曲/背景图（`theme.*`、`backdrops/`、`cover.jpg`） | 本期支持，见 FR-14 |
| 库类型 | 电影库与剧集库必须是两个独立库，不能互相混放 |

### 6.5 核对结论汇总

| # | 你的说法 | 结论 | 说明 |
| --- | --- | --- | --- |
| 1 | 电影要一个文件夹，里面放视频 | ✅ 正确 | 官方推荐做法 |
| 2 | 视频文件名一般与文件夹同名 | ✅ 正确 | 官方建议同名，多版本时必须字符级一致 |
| 3 | 剧集 = 剧名文件夹 / Season 01、Season 02… | ✅ 正确 | 但季目录必须写 `Season 01`，不可写 `S01` |
| 4 | 剧集文件名必须含 `SXXEXX`，季号与季文件夹一致、集号两位 | ✅ 正确 | 程序统一生成 `S01E02` 规范形式 |
| 5 | 扩展名保持原样，mkv/avi 不动 | ✅ 正确 | 程序只改主名，不动扩展名，不转码 |
| 6 | 同目录字幕文件跟随视频一起改名 | ✅ 正确 | 语言标签不映射（决策 12）；`VobSub` 需 `.idx`+`.sub` 成对改名 |

## 7. 功能需求

### FR-01 扫描与「传输完成」判定

- 递归扫描源目录（默认 `/data/upload`），忽略：
  - 隐藏文件/目录（`.` 开头）；
  - 临时/未完成文件：`*.part`、`*.!qB`、`*.tmp`、`*.aria2`、`*.crdownload`、`*.downloading`、`*.uploading`；
  - 0 字节文件；
  - 与影视无关的文件（`.nfo` 之外的非视频/字幕/图片/音频文件；`.nfo` 本期不处理，仅记录 `ignored`）。
- 附件（花絮视频、图片、主题曲）参与扫描，但要能识别为附件而不是独立条目（见 FR-14）。
- 「传输完成」判定：文件 `mtime` 与 `size` 在最近 N 秒内无变化（默认 `--stable-seconds 60`）；不满足则本轮跳过，记 `WAIT_UNSTABLE`。
- 支持重复执行（幂等）：同一批文件重复跑不会产生重复条目（见 FR-08）。

### FR-02 类型识别（电影 / 剧集 / 无法判断）

判定优先级（先高后低）：

1. 路径或文件名中出现 `Season XX` / `S01` 目录，或文件名中出现 `SxxExx`、`SxxExx-Eyy`、`第x集`、`EPxx` → **剧集**；
2. 源是单文件且无季集特征 → **电影**（按文件名解析片名/年份/版本）；
3. 源是文件夹：
   - 内含季目录（`Season *`、`S01`）或视频文件名含季集 → **剧集**；
   - 只有一个视频文件且无季集特征 → **电影**（文件夹名作为片名）；
   - 多个视频、无季集特征、但**片名相同且仅分辨率/版本不同** → **同一电影的多版本**（FR-13）；
   - 多份字幕/附件目录（`extras` 等）中的视频不计入「多个视频」判断（FR-14）；
   - 其余多视频情形 → 置信度 low，**默认不处理**，标记 `NEED_REVIEW`；
4. 文件名同时含 `SxxExx` 和电影年份、无法区分时 → 置信度 low，标记 `NEED_REVIEW`；
5. 都判断不出 → `UNKNOWN`，只进报告，不动文件（决策 6：原样留在 `/data/upload`）。

支持人工干预：

- 命令行 `--type movie|tv|auto`（默认 auto）；
- 配置项 `[[override]]` 按路径/正则强制指定类型（见 §9）。

### FR-03 元数据解析

**电影**

- 片名：取文件夹名（优先）或文件名，按 FR-03.1 的「保守清洗规则」去除发布标签（只删词表中的独立 token，不误删片名）；
- 年份：按以下顺序查找，取 1900–2100 范围内的数字：
  1. 文件名中的 `(2023)`、`[2023]`、`.2023.`、`_2023_`；
  2. 所在文件夹名中的同样形式；
  3. 库内已存在的同名条目目录——复用其年份，避免同一部片/同一部剧被拆成两个目录（`reuse_library_year = true`，默认开启）；
  4. 联网查询 TMDb/TVDB（`year_lookup = "tmdb"`，默认 `none`，本期未实现，见 FR-16）；
- **年份缺失（源文件名里确实没有）时**：
  - 不猜、不写假年份、不生成空括号 `()`；
  - 目标名直接省略年份段：`movies/片名/片名.ext`、`tvshows/剧名/Season 01/剧名 S01E01.ext`；
  - 报告与日志标 `WARN_NO_YEAR`（提示刮削可能因同名翻拍片而匹配偏差）；
  - 该条目不阻塞、不降级为「待人工确认」，正常入库；
  - 若库内已存在同名目录（如 `片名 (2023)`），则自动并入该目录并复用其年份（匹配到多个不同年份时标 `WARN_YEAR_AMBIGUOUS`，不做自动合并）；
- 解析失败（片名都提取不出）时：片名 = 原始主名（清洗后），置信度 medium。

**剧集**

- 剧名：取「剧集文件夹名」优先，其次文件名 `SxxExx` 之前的部分；
- 年份：同电影规则（含「年份缺失」策略），可选；
- 季号（决策 26）：文件名 `S(\d{1,2})` > 文件名里的 `第二季`/`3rd Season`/`Season 3` 等明写季号 > 目录名 `Season (\d{1,2})` / `第二季`；文件名与目录不一致时以文件名为准（报告标 `WARN_SEASON_MISMATCH`）；都没写才默认第 1 季并标 `SEASON_ASSUMED_1`；
- 集号：`E(\d{1,3})`，支持 `S01E01-E02`（多集一起），支持 `第03集`、`第3集`、`EP03`、`E03` 等中文/简写形式；**半集（17.5）保留原数字，输出 `E17.5`，不改成 E17/E18，也不留给人工决定（决策 27）**；
- **集标题**（决策 1：有就保留）：取 `SxxExx` 之后的内容（去掉前导分隔符 `.`/`-`/`_`/空格），再按 FR-03.1 清洗；清洗后非空则保留，如 `三体.S01E02.科学边界.mkv` → `三体 (2023) S01E02 科学边界.mkv`；为空则只输出 `S01E02`；
- 无季信息（只有集号）：默认归入 `Season 01`，并提示 `WARN_NO_SEASON`；
- 无集信息 → `NEED_REVIEW`。

### FR-03.1 保守清洗规则（决策 2）

原则：**只删除「被分隔符包围的完整 token」**，宁可少删，不能把片名删坏。

1. 预处理：`.`、`_`、连续空格、`[` `]` 一律视作分隔符转成空格；合并多余空格；去首尾 `-`。
2. 可删除 token 表（大小写不敏感，必须作为独立 token 命中）：

   | 类别 | token |
   | --- | --- |
   | 分辨率 | `2160p` `1080p` `1080i` `720p` `576p` `480p` `4k` `8k` `uhd` |
   | 来源 | `bluray` `blu-ray` `bdrip` `brrip` `web-dl` `webrip` `web` `hdtv` `hdtvrip` `dvdrip` `remux` `bdremux` |
   | 编码 | `x264` `x265` `h264` `h265` `hevc` `avc` `av1` `vp9` `10bit` `8bit` |
   | 音频 | `aac` `ac3` `ddp` `dd5.1` `dts` `dts-hd` `truehd` `atmos` `flac` `mp3` `5.1` `7.1` |
   | 画质 | `hdr` `hdr10` `hdr10+` `dv` `dolby vision` `sdr` |
   | 语言/字幕标注 | `国语` `粤语` `国粤` `中字` `中英` `双语` `简繁` `简中` `繁中` `chs` `cht` `gb` `gbk` `big5` `eng` |
   | 其他 | `proper` `repack` `rerip` `internal` `limited` |

3. **不算标签、不能删**：纯数字（`1917` 这类片名）、中文片名、未列入词表的英文单词。
4. 括号处理：`[Group]`、`{Group}` 视为发布组 → 删除；`(2023)` 提取为年份后删除；其它括号内容（如 `(导演剪辑版)`）保留原文。
5. 版本词（`extended`、`directors cut`、`imax`、`unrated`、`加长版`、`导演剪辑版`）**不删除**，转为多版本标签（见 FR-13）。
6. 清洗后为空、或只剩年份 → 回退使用原始主名（NFC 规范化后）。
7. 每次删除都记录到报告的 `cleaned_tokens` 字段，方便人工核对是否误删。

### FR-04 目标路径生成

**电影**

```text
目标 = {movies_dir}/{title}{ (year) }/{title}{ (year) }{version_tag}.{ext}
```

- `{title}` 为清洗后的片名，去掉首尾空格、合并连续空格；
- `{ (year) }` 存在则输出 ` (2023)`，**不存在则整段省略**（不会出现 `片名 ()`）；
- `{version_tag}` 仅在需要时生成（同一电影多版本并存时），形如 ` - 1080p`（与文件夹主名分隔符为 ` - `，字符级一致，规则见 FR-13）；
- 扩展名**按原样保留**（大小写也可保留，建议统一小写扩展名输入不动原样，如 `.MKV` 保持 `.MKV`）。

**剧集**

```text
目标 = {tvshows_dir}/{series}{ (year) }/Season {SS:02d}/{series}{ (year) } S{SS:02d}E{EE:02d}{title_part}.{ext}
```

- `Season {SS:02d}`：两位补零；99 以上按自然位数；
- `S{SS:02d}E{EE:02d}`：全部补零到两位；
- 文件名中的季号必须与所在季目录**一致**（程序生成时强制一致）；
- `{title_part}`：**默认保留集标题**（决策 1），形如 ` 科学边界`（前面一个空格）；无标题时整段省略；来源与清洗见 FR-03；可用 `--no-episode-title` 关闭；
- 同一集的多版本/多碟写法见 FR-13。

**年份未知时的实际结果（示例）**

```text
# 源：/data/upload/流浪地球2.1080p.mkv（文件名里没有年份）
movies/流浪地球2/流浪地球2.mkv
movies/流浪地球2/流浪地球2.srt

# 源：/data/upload/三体/S01E01.mkv（没有年份）
tvshows/三体/Season 01/三体 S01E01.mkv

# 库里已有 movies/流浪地球2 (2023)/ → 自动复用年份并入该目录
movies/流浪地球2 (2023)/流浪地球2 (2023).mkv
```

- 合并复用规则只在**唯一命中**时生效；若库内同时存在 `片名 (2018)` 与 `片名 (2023)`，则不复用、按无年份目录处理，并标 `WARN_YEAR_AMBIGUOUS`；
- 共用同一个「片名」目录是安全的：Jellyfin 按文件夹名 + 视频名识别，缺年份只会降低匹配置信度，不会导致无法识别。


**通用**

- 文件名长度超 255 字节时截断片名部分，保留 `(年份) SxxExx` 与扩展名（无年份时保留 `SxxExx` 与扩展名）；
- 统一 Unicode NFC；去除控制字符；保留字符 `<>:"/\|?*` 替换为 `-`（或直接删除，配置项 `sanitize`）；
- 不做全角/半角转换、不改中文名（用户可能希望保持中文标题）。

### FR-05 字幕处置（决策 12：不映射标签，名字与视频一致）

1. 识别外挂字幕扩展名：`.srt .ass .ssa .vtt .sub .idx .smi .ttml .dfxp`；
2. 与视频的配对规则（与 Jellyfin 源码一致：以视频主名为前缀 + 分隔符）：
   - 同目录，且字幕主名 = 视频主名（`Movie.mkv` ↔ `Movie.srt`）；
   - 或字幕主名 = 视频主名 + **`.`** + 任意标签（`Movie.chs.srt`、`Movie.zh-CN.ass`）；
     （源码确认：分隔符只有 `.`，`Movie-chs.srt` / `Movie_chs.srt` Jellyfin 不认）
   - `idx`/`sub` 必须成对一起改名；

> **阶段一现状**：只分析、不改名。报告里会给出每份字幕的「建议目标名」和状态
> （同目录配对 / 子目录待搬 / 压缩包内 / 配不上 / 格式不支持）。
> 下面 3–7 条是阶段二执行时的规则。
3. 目标命名（默认 `name_mode = "same_as_video"`）：
   - **1 个视频只配到 1 份字幕** → 目标 = 新视频主名 + 原扩展名（语言类标签去掉，例如 `Movie.chs.srt` → `片名 (2023).srt`）；
   - **例外**：若标签里含 `forced`/`foreign`/`sdh`/`cc`/`default` 这类**播放行为标志**，保留该标志（`片名 (2023).forced.srt`），因为删掉会改变 Jellyfin 的字幕行为；配置项 `keep_flags = true`（默认）；
   - **配到 ≥2 份字幕** → 为避免同名覆盖，保留原始标签段（**原样，不映射、不改写**），例如 `片名 (2023).chs.srt`、`片名 (2023).cht.srt`；
   - 配置可切到 `name_mode = "keep_tags"`：无论几份字幕都保留原标签（`片名 (2023).chs.srt`）；
4. **不做**任何语言标签映射（`chs` 不会变成 `zh-CN`）；语言标签的识别效果见 §6.3 澄清与 §14 决策记录；
5. 若字幕无法配对到任何视频 → 保留原名不动，标记 `ORPHAN_SUBTITLE` 并在报告中列出，由人工处理；
6. 字幕不单独移动，**随视频一起处理**（同一事务内），避免只改了视频名字幕没跟上；
7. 目标已存在同名不同内容字幕 → 按 FR-08 冲突处理，绝不静默覆盖。

示例：

```text
源：upload/流浪地球2.2023.1080p.mkv
    upload/流浪地球2.2023.1080p.chs.srt        （仅此一份）
→   movies/流浪地球2 (2023)/流浪地球2 (2023).mkv
    movies/流浪地球2 (2023)/流浪地球2 (2023).srt

源：upload/某某剧.S01E01.mkv
    upload/某某剧.S01E01.chs.ass                （两份字幕）
    upload/某某剧.S01E01.cht.ass
→   tvshows/某某剧/Season 01/某某剧 S01E01.mkv
    tvshows/某某剧/Season 01/某某剧 S01E01.chs.ass
    tvshows/某某剧/Season 01/某某剧 S01E01.cht.ass
```


### FR-06 预览（dry-run）与报告

- **默认行为就是 dry-run**（必须显式 `--apply` 才动磁盘）；
- 控制台输出（结构化、适合人工快速扫读）：

  ```text
  PLAN  电影  → movies/流浪地球2 (2023)/流浪地球2 (2023).mkv
        来源  upload/流浪地球2.2023.1080p.BluRay.x264.mkv  (12.4 GiB)
        字幕  upload/流浪地球2.2023.1080p.chs.srt → 流浪地球2 (2023).srt
  PLAN  电影  → movies/流浪地球2 (2023)/流浪地球2 (2023) - 2160p.mkv（多版本）
  WARN  剧集  → tvshows/Shogun/Season 01/Shogun S01E03.mkv（缺少年份）
  SKIP  未稳定 upload/xxx.mkv（60s 内仍有改动）
  ```

- 汇总行：`识别 X 个条目（电影 a / 剧集 b）；将移动 c 个文件；新增版本 d；待人工确认 e；跳过 f；失败 g`；
- **报告文件**（默认写程序目录下的 `logs/`，可通过 `--report-dir` 改）：
  - `YYYYmmdd-HHMMSS-preview.md`：给**人**看的完整报告。章节固定为：§1 总览 / §2 问题标记汇总 / **§3 必须人工干预**（前面已决定程序不动的条目，阶段二跳过）/ **§4 未定**（程序暂时无法确认的细节：只有「未标季号，暂按第 1 季」）/ §5 逐条目目标路径 / §6 扫描时跳过的内容。§3 与 §4 **不重复列同一个条目**；§5 明细按处理方式分三组并依次排列：**5.1 从未见过的问题（需人工确认）**、**5.2 需要人工干涉和处理**、**5.3 能够自动处理的**，编号沿用 `plan.tsv`；前两组带 🔴 红色标记（用行内 HTML `<span style="color:red">` 实现，SSH 纯文本下看到的是 🔴 + 中文标记），原因与要做的事和 §3 一致。已确认的集号原样排序（04 → S01E04）、半集原数字（17.5 → E17.5）、文件名明写季号等不再列入「未定」，只在 §2 与 §5 记录；
  - `YYYYmmdd-HHMMSS-preview.json`：完整结构化数据（每集、每份字幕、每个问题码）；
  - `YYYYmmdd-HHMMSS-plan.tsv`：一行一个条目的**确认表**，后续阶段二按它执行；
  - `YYYYmmdd-HHMMSS-apply.md` / `YYYYmmdd-HHMMSS-apply.json`：执行后生成的结果报告（阶段二）。
- **报告目录的硬性约束**：禁止落在 `movies/` 或 `tvshows/` 内，程序启动时校验，不合格直接退出（避免被 Jellyfin 当媒体扫描）。
- 报告里对每个条目额外给出 **`jf-ingest.md` 的计划位置**（以及内容预览），
  方便在执行前先确认「记录会写成什么样」；预览阶段不写盘（见 FR-17）。

> **关于「报告放哪里」（决策 10 的说明）**
>
> 报告目录**不是 Jellyfin 的库目录**，在 Jellyfin 前台/客户端里**看不到**它，也不会被当成媒体扫描。它就是一堆普通文件（Markdown / JSON / HTML），用来看「这批文件准备怎么处理」。
>
> - 默认位置：程序目录下的 `logs/`（例如 `/opt/jf-ingest/logs/`），手动执行完直接 `cat`、`less`、`vi`，或 `scp` 拉到本地用编辑器/浏览器看；Markdown 是 UTF-8 纯文本，SSH 终端设成 UTF-8 就能直接看中文；
> - 放 `/data/jellyfin/.ingest-reports` 也不是给 Jellyfin 看的，只是借那块磁盘；**绝不能**放进 `movies/`、`tvshows/` 里面——那会被当成媒体内容扫描；
> - 想方便点开：`--html` 生成 HTML 后，把 `logs/` 挂到 Nginx/Samba，或用 `--report-dir` 指到一个已有的共享目录即可。
- 报告与日志中不输出文件内容，仅路径与元信息。

### FR-07 执行（apply）

- 三种落盘策略（配置 `mode`，**默认 `move`（决策 3）**）：
  - `move`：跨分区时 `shutil.move`，同分区尽量 `os.replace`；
  - `copy`：复制并保留 `mtime`/权限，源文件保留（适合保种）；
  - `hardlink`：同一文件系统内建硬链接（省空间，源文件删除不影响库）；
- 目标目录不存在则创建（`0755`），文件权限 `0644`；执行完成后统一 `chown` 到 `jellyfin:jellyfin`（默认开启，见 FR-15）；
- 顺序：先确保目标目录 → 移动视频 → 处理字幕/附件 → `chown` → 记录结果；任一文件失败不影响同批其它条目，但该条目整体标记失败（视频成功、字幕失败时在报告中明确标出）；
- `--prune-empty-dirs`（默认关闭）：处理完后删除源目录中已变空的目录；
- 不覆盖已存在文件（见 FR-08）；同一电影/剧集的多版本并存按 FR-13 生成带版本标签的新文件，**不算冲突**。

### FR-08 冲突与幂等

| 情况 | 处理 |
| --- | --- |
| 目标文件已存在，且大小/内容一致（size + mtime 或可选 hash） | 跳过，标记 `SKIP_SAME`（幂等） |
| 目标文件已存在但内容不同 | **不覆盖**，标记 `CONFLICT`，进入待人工确认；可选 `--on-conflict suffix` 生成 `片名 (2023) (2).mkv` |
| 同一电影已有 1080p，新到 2160p（片名/年份相同） | 按多版本处理：生成 `片名 (2023) - 2160p.mkv` 与已有版本并存，不覆盖、不算冲突（见 FR-13） |
| 同一集已有其它版本（相同 `SxxExx`） | 同上：`剧名 (2023) S01E01 - 1080p.mkv`（见 FR-13） |
| 字幕目标名已存在但内容不同（多份字幕同名） | 标 `CONFLICT`，不覆盖；报告中提示把源字幕补标签或人工处理 |
| 同一批内两个源映射到同一目标 | 保留先出现的，后者标记 `CONFLICT` |
| 目标目录已有该剧集/电影但缺季目录 | 自动补建季目录 |
| 库内已有同名不同年份 | 视为不同条目，正常写入（年份区分子目录） |

### FR-09 日志（关键）

- 双通道：控制台（默认 `INFO`，`--verbose` 为 `DEBUG`）+ 文件；
- 文件：`logs/jf-ingest.log`，`RotatingFileHandler`（10 MiB × 5），UTF-8；
- 每行包含：`时间 | 级别 | run_id | 事件码 | 消息 | 关键字段`；
- 关键事件码（示例）：

  | 事件码 | 含义 |
  | --- | --- |
  | `SCAN_START` / `SCAN_END` | 扫描开始/结束（含文件数、耗时） |
  | `DETECT_TITLE` | 识别结果（类型、片名、年份、季、集、置信度） |
  | `DETECT_AMBIGUOUS` | 无法确定，需人工 |
  | `PLAN_ITEM` | 计划条目（源 → 目标） |
  | `MOVE_OK` / `COPY_OK` / `LINK_OK` | 落盘成功 |
  | `SUB_RENAMED` / `SUB_ORPHAN` | 字幕处理结果 |
  | `SKIP_*` | 跳过原因（unstable/ignored/same） |
  | `CONFLICT` | 目标冲突 |
  | `VERSION_ADDED` | 多版本并存（命中版本标签） |
  | `EXTRA_PLANNED` / `EXTRA_OK` | 花絮/预告/图片/主题曲处理 |
  | `YEAR_REUSED` | 复用库内已有年份 |
  | `CHOWN_OK` / `CHOWN_SKIP` / `CHOWN_FAIL` | 属主设置结果 |
  | `REFRESH_OK` / `REFRESH_FAILED` | Jellyfin 库刷新结果 |
  | `ERROR` | 异常（含堆栈，写文件不写控制台） |
  | `REPORT_WRITTEN` | 报告路径 |

- 敏感信息：不记录 Jellyfin API Key（若启用 FR-12）。

### FR-10 配置

- 优先级：命令行参数 > 配置文件 > 默认值；
- 配置文件默认 `config.toml`（UTF-8，Python 3.11+ `tomllib` 读取），示例见 §9；
- 所有路径支持绝对路径，也支持 `~` 展开。

### FR-11 CLI

```text
jf-ingest [preview|apply] [options]

# —— 阶段一已实现 ——
  --config PATH            配置文件（JSON，默认内置值）
  --source PATH            源目录（默认 /data/jellyfin/uploads）
  --movies PATH            电影库（默认 /data/jellyfin/movies）
  --tvshows PATH           剧集库（默认 /data/jellyfin/tvshows）
  --report-dir PATH        报告输出目录（默认 ./logs）
  --from-listing PATH      从清单 TSV 分析（离线，不读磁盘）
  --only 3,17              只分析指定序号
  --limit N                只分析前 N 个条目
  --map-subtitle-language  字幕标签映射成 zh-CN/zh-TW（默认关）
  --max-episodes N         Markdown 里每条目最多展示几集
  --verbose
  --version

# —— 阶段二：按确认表执行（默认 move，不覆盖）——
  apply --plan logs/xxx-plan.tsv
  --mode move|copy|hardlink
  --on-conflict skip|suffix
  --prune-empty-dirs
  --chown USER:GROUP / --no-chown
  --refresh / --no-refresh
  --no-manifest                   不写 jf-ingest.md（FR-17）
  --manifest-name NAME            改清单文件名（默认 jf-ingest）
  --manifest-format md|txt        清单格式（默认 md）
```

退出码：

| 码 | 含义 |
| --- | --- |
| 0 | 预览成功（阶段一）/ 全部成功（阶段二） |
| 1 | 部分条目失败/冲突（阶段二） |
| 2 | 参数、配置或确认表错误 |
| 3 | 没有扫描到任何文件 / 没有可执行条目 |

### FR-12 通知 Jellyfin 刷新（决策 7：需要）

- 迁移成功（退出码 0 或 1）后，调用 Jellyfin API 触发刷新；`--no-refresh` 可跳过；
- 两种模式（配置 `refresh_mode`）：
  - `"paths"`（**默认**）：`POST /Library/Media/Updated`，请求体
    `{"Updates":[{"Path":"<目标路径>","UpdateType":"Created"}]}`，只刷新本次变动的条目，开销小；
  - `"all"`：`POST /Library/Refresh`，全库扫描（库大时较慢，但最稳）；
- 认证：请求头 `Authorization: MediaBrowser Token="<api_key>"`；Key 从配置或环境变量 `JF_API_KEY` 读取，**日志与报告里脱敏**；
- 仅用标准库 `urllib.request`，超时 10s；连接失败/401/5xx 只告警（`REFRESH_FAILED`），不改变迁移结果与退出码；
- apply 开始前先做一次连通性探测（失败则提前提示「刷新会失败，但迁移仍继续」）。

### FR-13 多版本与多碟（决策 4：需要）

**判定**：目标位置（同片名+年份 / 同剧集+季+集）已存在视频时，新文件视为「同一条目的另一个版本」。

**版本标签来源（按优先级）**：

1. 分辨率：`2160p` / `1080p` / `720p` / `480p`（从文件名提取并统一小写）；
2. 画质：`HDR` / `DV`（文件名无分辨率时使用）；
3. 版本词：`Director's Cut` / `Extended` / `Uncut` / `IMAX` / `加长版` / `导演剪辑版`（FR-03.1 明确不删除的词）；
4. 以上都没有 → `v2`、`v3`（同目录内按已有版本数递增）。

**命名**（版本段必须与文件夹主名严格以分隔符相连）：

```text
movies/片名 (2023)/片名 (2023) - 1080p.mkv
movies/片名 (2023)/片名 (2023) - 2160p.mkv
tvshows/剧名 (2023)/Season 01/剧名 (2023) S01E01 - 1080p.mkv
```

- 分隔符固定为 ` - `（空格 + 连字符 + 空格），版本词按原文保留、只做非法字符替换；
- 多碟/多段：`片名 (2023) - cd1.mkv`、`片名 (2023) - part2.mkv`（官方支持 `cd`/`dvd`/`part`/`pt`/`disc`/`disk`）；
- 同一电影的多版本**必须落在同一文件夹**，否则 Jellyfin 会当成两部电影；
- 处理顺序：先判多版本 → 再判同名同内容（`SKIP_SAME`）→ 最后才 `CONFLICT`；
- 报告标 `VERSION_ADDED` 并注明「新增第 N 个版本」。

### FR-14 花絮 / 预告 / 主题曲 / 图片（决策 5：需要）

| 源形态 | 目标位置 | 说明 |
| --- | --- | --- |
| 子目录 `extras` `trailers` `behind the scenes` `deleted scenes` `interviews` `scenes` `samples` `shorts` `featurettes` `clips` `other` | 所属电影/剧集（或季）目录下，同名子目录 | Jellyfin 按这些目录名识别花絮类型 |
| 文件名后缀 `-trailer` `.trailer` `_trailer` ` trailer` | 与视频同目录，保留原文件名 | 后缀本身即可被识别，不必再建目录 |
| 文件名后缀 `-sample` `.sample` `_sample` 及 `-behindthescenes` `-deleted` `-featurette` `-interview` 等 | 与视频同目录，保留原文件名 | 同上 |
| `theme.mp3` / `theme.flac` / `theme.*` | 电影或剧集根目录 | 主题曲 |
| `backdrops/*.jpg` 或 `backdrops/*.png` | 电影/剧集/季目录下 `backdrops/` | 背景图 |
| `cover.jpg` `poster.jpg` `folder.jpg` | 电影/剧集/季目录根 | 封面（Jellyfin 识别这些文件名） |
| `logo.png` `clearlogo.png` `banner.jpg` `thumb.jpg` | 同目录 | 其它图片元数据 |

- 花絮/图片必须放在**所属条目**目录内，不能放库根，否则会被当成独立影片；
- 花絮不做电影名/`SxxExx` 重命名（避免破坏 extras 识别），只跟随宿主目录移动，目录名统一成 Jellyfin 认可的官方小写名；
- 花絮带外挂字幕时按 FR-05 规则处理；
- 无法归属到任何条目的孤立附件 → 保留在 `/data/upload`，报告 `ORPHAN_EXTRA`，不猜归属；
- 日志记录 `EXTRA_PLANNED` / `EXTRA_OK`。

### FR-15 权限归属与运行方式（决策 8、9）

**属主（chown）**：

- 默认 `jellyfin:jellyfin`，作用于本次创建/移动的所有文件与目录；
- 以 root 运行 → 直接 `os.chown`（目录 `0755`、文件 `0644`）；
- 以非 root 运行、且当前用户不是目标属主 → 标 `CHOWN_SKIP` 告警，不判定失败；
- `--no-chown` 关闭；`--chown user:group` 覆盖；
- `move` 时保留原 `mtime`；`copy` 时用 `shutil.copystat` 同步权限与时间。

**运行方式：手动执行**（不内置 cron/systemd）：

```bash
cd /opt/jf-ingest
sudo .venv/bin/python -m jf_ingest preview        # 先看报告
sudo .venv/bin/python -m jf_ingest apply          # 确认后执行
```

- 提供 `bin/ingest.sh` 包装脚本（自动切到程序目录、加载配置、参数透传），避免每次输长路径；
- 程序支持在任意工作目录下运行：默认配置/报告/日志路径按**程序安装目录**解析，不依赖 CWD。

### FR-16 联网补年份（预留，本期不实现，决策 11）

- 配置项先留好：`year_lookup = "none" | "tmdb"`，**默认 `none`**；库内年份复用由独立开关 `reuse_library_year = true` 控制（与联网无关）；
- 代码抽象 `YearResolver` 接口（`resolve(title, kind) -> int | None`）：
  - 本期实现 `NoneYearResolver`（返回 None）与 `LibraryYearResolver`（查库内同名目录，默认启用）；
  - `TmdbYearResolver` 只留空实现 + TODO：`GET /search/movie|tv?query=&year=`，取 `release_date`/`first_air_date` 年份，带缓存与超时；
- 网络不可用/未配 Key 时静默回退 `None`，不报错、不阻塞；
- 报告中记录 `year_source = filename | folder | library | tmdb | none`，便于以后排查。

### FR-17 入库记录文件（写在条目文件夹内，决策 24）

**目的**：入库之后，即使 `logs/` 被清掉、库被整体拷到别的机器，也能在库里直接看出
「这个文件夹原来叫什么、哪些文件改过名」。不依赖程序目录，不依赖数据库。

**写在哪里**（关键：是「条目文件夹」，不是季文件夹）：

| 类型 | 写入位置 | 结果 |
| --- | --- | --- |
| 电影 | 电影文件夹根目录 | `movies/片名 (年份)/jf-ingest.md` |
| 剧集 | **剧名文件夹根目录**（`Season 0X` 的**上一级**） | `tvshows/剧名 (年份)/jf-ingest.md` |

- 剧集一个条目只写**一份**，多季合并进同一个文件；`Season 01/`、`Season 02/` 里**不放**；
- 文件名默认 `jf-ingest.md`，可配置：`manifest.enabled` / `manifest.name` / `manifest.format`；
- 默认 `.md`（表格和代码块可读性好）。想用纯文本可设 `format = "txt"`（内容相同，去掉 Markdown 标记）；
- 名字里带 `jf-ingest` 是刻意的：一眼能看出是程序生成的，不会和用户自己的 `说明.txt` 混淆。

**内容**（固定四段，UTF-8 无 BOM，`\n` 换行）：

| 段 | 内容 |
| --- | --- |
| ① 摘要 | 片名/剧名、年份、类型、**源目录名**、首次建档时间、最后更新时间、本次结果（成功 / 部分失败 / 跳过） |
| ② 原始结构 | 源条目在 `uploads` 里的完整树（相对源条目根），**保留原始目录名和文件名**，一字不改（含 `[ ]`、点号、站点水印） |
| ③ 当前结构 | 目标文件夹的完整树（相对该文件夹），即 Jellyfin 现在看到的样子 |
| ④ 改名对照 | **一行一条**：`原始相对路径 → 当前相对路径`；覆盖所有被移动/改名的文件（视频、字幕、花絮），**没搬运的文件也列出来**并标 `SKIP` + 原因 |

示例（`movies/流浪地球2 (2023)/jf-ingest.md`）：

````markdown
# 流浪地球2 (2023)

- 类型：电影
- 源目录：`[BDrip] The.Wandering.Earth.II.2023.1080p`
- 首次建档：2026-11-19 18:11:51 ｜ 最后更新：2026-11-19 18:11:51
- 结果：成功（2 个文件）

## 原始结构

```text
[BDrip] The.Wandering.Earth.II.2023.1080p/
├── The.Wandering.Earth.II.2023.1080p.mkv
└── The.Wandering.Earth.II.2023.1080p.chs.srt
```

## 当前结构

```text
流浪地球2 (2023)/
├── 流浪地球2 (2023).mkv
├── 流浪地球2 (2023).chs.srt
└── jf-ingest.md
```

## 改名对照

| 原始 | 当前 |
| --- | --- |
| `The.Wandering.Earth.II.2023.1080p.mkv` | `流浪地球2 (2023).mkv` |
| `The.Wandering.Earth.II.2023.1080p.chs.srt` | `流浪地球2 (2023).chs.srt` |
````

剧集示例（`tvshows/三体 (2023)/jf-ingest.md`，节选）：

````markdown
## 改名对照

| 原始 | 当前 |
| --- | --- |
| `S01/三体.S01E01.1080p.mkv` | `Season 01/三体 (2023) S01E01.mkv` |
| `S01/三体.S01E01.chs.ass` | `Season 01/三体 (2023) S01E01.chs.ass` |
| `S01/三体.S01E02.1080p.mkv` | `Season 01/三体 (2023) S01E02 科学边界.mkv` |
| `SP/三体.NCOP.mkv` | `extras/三体.NCOP.mkv` |
| `S01/三体.S01E03.1080p.mkv` | `SKIP`：目标已存在且内容不同（CONFLICT） |
````

**规则细节**：

1. 路径一律用 `/`，一律写**相对路径**（不写 `/data/...` 绝对路径），这样库整体搬家后文件依然有效；
2. 文件名里的 Markdown 特殊字符（`[` `*` `_` `|`）必须放进反引号或代码块，否则表格会错乱；
   `format = "txt"` 时对照表写成一行一条 `abc.avi  --  abc2004.avi`（就是你给的例子），Markdown 里则是表格的一行，语义完全相同；
3. 不写文件内容、不算 hash（库里 896 GB，清单只记名字和大小即可）；
4. **按实际结果写**：只记录真正落盘成功的文件；失败/跳过的放进「未完成」小节并写明原因，
   不允许「清单说搬了、实际没搬」；
5. **幂等 + 增量合并**：目标文件夹里已有 `jf-ingest.md` 时，读取 ④ 的表格，
   保留旧行、追加新行（按「当前路径」去重），刷新「最后更新」，**保留「首次建档」**；
   这样以后补传 `Season 02` 不会把第一季的记录冲掉；
6. 已有同名文件但**不是本程序写的**（没有约定的标题行）→ 不改写、不删除，
   另存为 `jf-ingest.<YYYYmmdd-HHMMSS>.md`，报告标 `MANIFEST_CONFLICT`；
7. 只在 `apply` 且条目**成功或部分成功**时写；`NEED_REVIEW` / `DUP_RELEASE` / `NOT_MEDIA`
   这类「整条不动」的条目不写（避免给没入库的东西留下假记录）；
8. 权限跟随目录（文件 `0644`），属主处理见 FR-15；
9. `preview` 阶段**不写盘**，只在报告里给出「将写入 `<路径>`」和内容预览；
10. 文件本身不参与扫描：它位于库目录，不在源目录；下次扫描 `uploads` 时也不会看到它。

## 8. 非功能需求

| 编号 | 要求 |
| --- | --- |
| NFR-1 | 安全性：默认 dry-run；绝不覆盖已有非同名内容；写入路径必须位于配置的两个库目录内（防目录穿越） |
| NFR-2 | 幂等性：重复执行不产生重复项、不产生副本 |
| NFR-3 | 性能：1 万文件规模扫描 + 规划 < 30s（不含实际复制）；不使用全量 hash 作为默认判重 |
| NFR-4 | 健壮性：单个条目异常不影响其它条目；所有异常写入日志并给出可执行建议 |
| NFR-5 | 可测试性：解析/命名/规划为纯函数，可用单元测试覆盖；集成测试用临时目录模拟全流程 |
| NFR-6 | 可观测性：日志 + 报告 + 摘要三份信息口径一致 |
| NFR-7 | 可移植性：核心逻辑不依赖 Windows 专有 API；符号链接默认不跟随（`--follow-symlinks` 可选） |
| NFR-8 | 兼容性：Python 3.12 / 3.13 均可运行；默认零第三方依赖 |
| NFR-9 | 权限：目标文件 `0644`、目录 `0755`，属主默认 `jellyfin:jellyfin`；非 root 时跳过并告警，不失败 |
| NFR-10 | 离线可用：`year_lookup` 默认 `none`，程序在无外网环境下功能完整（TMDb 仅预留） |
| NFR-11 | 与 Jellyfin 解耦：刷新失败不影响迁移；迁移本身不依赖 Jellyfin 在线 |

## 9. 配置示例（`config.json`）

> 目标机 Python 3.10 没有 `tomllib`，也装不了 `tomli`，所以配置改用 **JSON**。
> JSON 不支持注释，字段含义见下表；也可以直接复制 `config.example.json`。

```json
{
  "paths": {
    "source": "/data/jellyfin/uploads",
    "movies": "/data/jellyfin/movies",
    "tvshows": "/data/jellyfin/tvshows",
    "reports": "./logs"
  },
  "behavior": {
    "mode": "preview",
    "stable_seconds": 0,
    "keep_episode_title": true,
    "max_episodes_in_md": 5
  },
  "subtitle": {
    "enabled": true,
    "map_language": false
  },
  "manifest": {
    "enabled": true,
    "name": "jf-ingest",
    "format": "md",
    "merge": true,
    "include_skipped": true
  }
}
```

字段说明（阶段一实际读取的）：

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `paths.source` | 路径 | 扫描起点，默认 `/data/jellyfin/uploads` |
| `paths.movies` / `paths.tvshows` | 路径 | 库里两个根目录，只用于报告展示与安全检查 |
| `paths.reports` | 路径 | 报告目录；**绝不允许落在 movies/tvshows 里**（程序会拒绝） |
| `behavior.stable_seconds` | 秒 | 文件最近 N 秒内有变化则跳过，0 = 关闭（预览阶段推荐 0） |
| `behavior.keep_episode_title` | bool | 有集标题时是否写进文件名 |
| `behavior.max_episodes_in_md` | int | Markdown 里每个条目最多展示几集（完整数据在 JSON） |
| `subtitle.enabled` | bool | 是否分析字幕（阶段一只记录，不改名） |
| `subtitle.map_language` | bool | 是否把 `sc/tc/chs/cht` 映射成 `zh-CN/zh-TW`（默认关，见决策 13） |

**阶段二已读取的字段**：
`behavior.mode`(默认 move)、`behavior.on_conflict`(默认 skip)、`behavior.prune_empty_dirs`、
`permissions.*`、`jellyfin.*`、`manifest.*`（见下表）。

**仍为预留、程序不读的字段**：
`follow_symlinks`（暂由内部默认值控制）、`reuse_library_year`、`year_lookup`、`year_map`、
`subtitle.name_mode`、`subtitle.keep_flags`、`extras.enabled`、`versions.*`、`override[]`。
语义与原来的 TOML 版本一致，后续需要时再实现。

其中 `manifest.*` 对应 FR-17，语义如下：

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `manifest.enabled` | bool | 是否在条目文件夹里写 `jf-ingest.md`（默认 `true`） |
| `manifest.name` | 文件名（不含扩展名） | 默认 `jf-ingest`；改成 `迁移记录` 也可以 |
| `manifest.format` | `md` \| `txt` | 默认 `md`；`txt` 内容相同、无 Markdown 标记 |
| `manifest.merge` | bool | 已存在同名清单时是否增量合并（默认 `true`，见 FR-17 规则 5） |
| `manifest.include_skipped` | bool | 是否把未搬运/冲突的文件也列进对照表（默认 `true`） |

## 10. 关键流程

```text
1. 读取配置 + 解析参数（手动执行，决策 9）
2. 扫描源目录 → 过滤临时文件/未稳定文件
3. 分组：视频 + 同目录字幕 + 花絮/图片/主题曲 → 条目
4. 识别：类型（movie/tv） + 元数据（片名/年份/季/集/集标题） + 置信度
5. 版本判定：同一电影/同一集已有其它版本 → 生成版本标签（FR-13）
6. 生成目标路径（年份复用、冲突检测、字幕命名）
7. 预览：控制台摘要 + MD/JSON(/HTML) 报告
   └─ 未 --apply → 结束（退出码 0/3）
8. 执行：建目录 → 移动视频 → 字幕/附件改名 → 校验大小 → chown jellyfin
9. 写 `jf-ingest.md` 入库记录（每个条目文件夹一份，FR-17；增量合并已有记录）
10. 写 apply 报告 + 汇总日志
11. 触发 Jellyfin 刷新（paths / all，失败仅告警）
```

## 11. 边界与异常处理

| 场景 | 期望行为 |
| --- | --- |
| 视频文件损坏 / 0 字节 | 跳过并提示，不移动 |
| 文件名含 `/` 等非法字符 | 替换为 `-`，报告中标 `SANITIZED` |
| 超长文件名（>255 字节） | 截断片名保留季集与扩展名 |
| 中文片名 | 原样保留（NFC），不做拼音化 |
| 源文件名/目录名里没有年份 | 正常入库，目标名省略 `(年份)`，报告标 `WARN_NO_YEAR`；库内已有同名目录则复用其年份 |
| 库内同名但年份不同（多个命中） | 不自动合并，标 `WARN_YEAR_AMBIGUOUS`，按无年份目录处理 |
| 同一剧集散落在多个上传文件夹 | 允许合并到同一目标剧集目录（季目录分别创建） |
| 文件名含 `S01E01` 但视频实为合集（多集） | 按 `S01E01-E02` 处理（见 FR-03 / FR-04） |
| 同一视频有 2 份以上字幕 | 保留原始标签段区分（`片名.chs.srt` / `片名.cht.srt`），绝不互相覆盖（FR-05） |
| 目标已有同名电影但分辨率不同 | 视为多版本，生成 ` - 1080p` / ` - 2160p` 并存（FR-13） |
| 花絮/图片无法归属到任何条目 | 留在 `/data/upload`，报告 `ORPHAN_EXTRA`，不猜宿主 |
| 目标磁盘空间不足 | 提前检查（`shutil.disk_usage`），不足则直接失败并说明还差多少 |
| 跨分区移动 | 复制 + 校验大小 + 删除源（move 语义），失败保留源 |
| 中断（Ctrl+C / kill） | 已完成条目保持；未完成目标文件删除残留；日志记录中断 |
| 源目录不存在/不可读 | 退出码 2，明确提示 |
| 库目录未挂载（路径不存在） | 默认退出码 2；`--create-missing-dirs` 可自动创建 |
| 以非 root 运行但要求 chown | 跳过属主修改，标 `CHOWN_SKIP`，迁移照常完成（不失败） |
| Jellyfin API 不可达 / API Key 无效 | 刷新失败只告警（`REFRESH_FAILED`），退出码与原迁移结果一致 |
| 目标目录无写权限 | 该条目失败，退出码 1，报告写明需要的权限（如 `/data/jellyfin/tvshows` 需可写） |
| 条目文件夹里已有 `jf-ingest.md`（本程序写的） | 读取旧对照表，增量合并新行，保留「首次建档」，刷新「最后更新」（FR-17 规则 5） |
| 条目文件夹里已有同名文件但不是本程序写的 | 不覆盖、不删除，另存 `jf-ingest.<时间>.md`，报告标 `MANIFEST_CONFLICT` |
| 条目部分失败（视频成功、字幕失败） | 清单按**实际结果**写，只记成功的；失败的进「未完成」小节并写原因 |
| 目录名/文件名里含 `[` `*` `_` `|` | 清单里放进反引号或代码块，保证 Markdown 不乱码 |

## 12. 测试要求

1. 单元测试（`tests/`）：
   - 片名/年份解析：≥ 30 个真实风格文件名（含中文、发布组、年份缺失）；
   - 保守清洗：`1917`、`三体`、`4K`（片名本身）不被误删；`1080p`、`BluRay`、`x265`、`中字` 被正确删除；
   - 季集解析：`S01E02`、`s1e2`、`S01E01-E02`、`第03集`、`EP03`、`1x02`、季目录与文件名冲突、集标题提取；
   - 目标路径生成：电影/剧集/多版本/多碟/超长名/非法字符/年份缺失；
   - 字幕配对与命名：单份（去语言标签同名）、单份带 `forced`（保留标志）、多份（保留标签）、`idx+sub` 成对、孤儿字幕；
   - 多版本判定：同片不同分辨率 → 版本标签；`v2`/`v3` 回退；
   - 花絮/图片归类与 `ORPHAN_EXTRA`；
   - 冲突与幂等判定；chown 非 root 时 `CHOWN_SKIP`；
   - **入库记录（FR-17）**：四段结构完整；原始名/新名一一对应；`SKIP` 行带原因；
     已有清单时增量合并不丢旧行；非本程序写的同名文件触发 `MANIFEST_CONFLICT`；
     `.md` 与 `.txt` 两种格式；文件名含 `[` `|` `*` 时不破坏结构；
   - Jellyfin 刷新：用 `unittest.mock` 打桩 `urllib`，校验请求路径/头/请求体，失败不影响结果。
2. 集成测试：临时目录构造 §5.1 的 A–H 八种形态 + 多版本 + 花絮，跑 preview 与 apply，断言目标树、属主调用、报告与刷新请求；
   其中要断言：电影文件夹与剧名文件夹根目录各有 `jf-ingest.md`，`Season 0X/` 里没有；二次 apply 后旧行仍在。
3. 手工验收：在真实 `/data/upload` 放一组样本，预览 → 执行 → Jellyfin 扫描结果正确（见 §13）。

## 13. 验收标准

| 编号 | 验收项 |
| --- | --- |
| AC-1 | `jf-ingest preview` 不改动任何文件，能列出每个视频的目标路径、类型、置信度、警告 |
| AC-2 | `jf-ingest apply` 后电影落入 `movies/片名 (年份)/片名 (年份).ext`，扩展名与原文件一致（字节级对比未变） |
| AC-3 | 剧集落入 `tvshows/剧名 (年份)/Season 01/剧名 (年份) S01E02.ext`，季号与文件名一致 |
| AC-4 | 带字幕的样本：字幕与视频同主名、同目录；单份字幕完全同名（如 `片名 (2023).srt`），多份字幕保留原标签且不互相覆盖 |
| AC-5 | 重复执行 apply 不产生重复文件、不覆盖已有文件 |
| AC-6 | 无法识别的样本不被移动，出现在报告的「待人工确认」中 |
| AC-7 | 日志包含扫描/识别/移动/失败的关键事件，可按 run_id 串起一次执行 |
| AC-8 | 报告文件（MD + JSON）可打开、内容与实际执行结果一致 |
| AC-9 | Jellyfin 扫描上述库后，电影/剧集均被正确识别（名称、年份、季、集、字幕可选） |
| AC-10 | 源文件名完全没有年份时：目标为 `movies/片名/片名.ext`、`tvshows/剧名/Season 01/剧名 S01E01.ext`，不出现空括号 `()`，报告标 `WARN_NO_YEAR`，Jellyfin 仍能识别 |
| AC-11 | 同一部剧分两次上传（一次带年份、一次不带）时，若库内已存在该剧目录，则并入同一目录，不产生两套剧集 |
| AC-12 | 同一电影先后放入 1080p 与 2160p 两个文件：库中同时存在 `片名 (2023) - 1080p.mkv` 与 `片名 (2023) - 2160p.mkv`，Jellyfin 显示为同一部电影的两个版本 |
| AC-13 | 剧集文件名保留集标题：`剧名 (2023) S01E02 科学边界.mkv`，Jellyfin 仍能正确识别季/集 |
| AC-14 | 花絮/预告/主题曲/封面按 FR-14 落到宿主目录，Jellyfin 在详情页能看到「花絮 / 预告」等分类 |
| AC-15 | apply 结束后目标文件属主为 `jellyfin:jellyfin`、权限 `644/755`；非 root 执行时只告警不失败 |
| AC-16 | apply 结束后调用了 `/Library/Media/Updated`（paths 模式）或 `/Library/Refresh`（all 模式）；API 不可达时迁移仍成功、退出码不变 |
| AC-17 | 报告与日志中对 API Key 做了脱敏（不出现明文 Key） |
| AC-18 | 每个成功入库的**电影文件夹**和**剧名文件夹**根目录各有一份 `jf-ingest.md`；剧集的 `Season 0X/` 里**没有**该文件；文件含「原始结构 / 当前结构 / 改名对照」三段，且能看出 `abc.avi → abc2004.avi` 这类改名 |
| AC-19 | 给同一部剧补传 `Season 02` 后再次 apply：`jf-ingest.md` 保留「首次建档」时间和第一季的旧行，追加第二季新行，不产生重复行 |
| AC-20 | `jf-ingest.md` 不会被 Jellyfin 当成媒体/字幕：扫描后既不多出影片，也不多出字幕轨 |

## 14. 决策记录（2026-11-19 评审确认）

| # | 问题 | 决策 | 对应实现 |
| --- | --- | --- | --- |
| 1 | 剧集集标题 | **有就保留**（如 `三体 (2023) S01E02 科学边界.mkv`） | FR-03、FR-04 |
| 2 | 片名清洗力度 | **按已知标签词表保守清洗**，只删独立 token | FR-03.1 |
| 3 | 源文件处理 | **默认 `move`** | FR-07 |
| 4 | 多版本 / 多碟 | **需要**，同一电影多分辨率并存 | FR-13 |
| 5 | 花絮/预告/主题曲/图片 | **需要**，一并整理并可被 Jellyfin 识别 | FR-14 |
| 6 | 无法识别时 | **原样留在 `/data/upload`**，报告列出，不猜、不强制 | FR-02 |
| 7 | 刷新 Jellyfin | **需要**，程序调 API 触发 | FR-12 |
| 8 | 属主 | **需要**，`jellyfin:jellyfin` | FR-15 |
| 9 | 执行方式 | **手动执行**，不内置定时/监听 | FR-15 |
| 10 | 报告位置 | 程序目录 `logs/`；**不是 Jellyfin 库目录，前台看不到** | FR-06 说明；决策 25 |
| 11 | 年份补全 | **暂不联网**；`year_lookup` 默认 `none`，TMDb 只预留接口 | FR-16 |
| 12 | 字幕标签 | **不映射**；字幕名改成与视频一致（多份字幕时保留原标签） | FR-05 |

### 14.3 第二轮决策（2026-11-19，看过真实数据后）

> 背景：`/data/jellyfin/uploads` 实测 1984 个文件 / 896 GB / 1297 个视频 / 203 个条目，
> 详细分析见 [`doc/inventory/upload-analysis.md`](inventory/upload-analysis.md)。

| # | 问题 | 决策 | 实现 |
| --- | --- | --- | --- |
| 13 | 字幕 `sc`/`tc` 是否映射成 `zh-CN`/`zh-TW` | **先不处理，只记录**（报告里列出每份字幕的标签和建议名） | 已实现：`subtitle.map_language = false`，报告输出建议名 |
| 14 | 压缩包里的字幕（24 个包，`.7z`/`.rar` 为主） | **先不处理，只记录** | 已实现：报告标 `SUBS_IN_ARCHIVE` 并列出包名 |
| 15 | 同一部剧两套 release（Hensuki） | **整个条目不动，人工决定** | 已实现：标 `DUP_RELEASE`，不给目标路径 |
| 16 | 73 个剧集没有季号 / 史莱姆第三季 `48.5-72` 连续编号 | **先记录，看过再说**；集号一律沿用原文件里的数字，只按数字排序（如 `04.mp4` `05.mp4` `06.mp4` → `S01E04` `S01E05` `S01E06`），**不重编号、不补 01–03**；半集按原数字当普通集（`E17.5`） | 已实现：标 `SEASON_ASSUMED_1` / `EP_RANGE_SUSPECT`，不自动加减偏移；`HALF_EPISODE` 已取消 |
| 17 | 第一次执行的节奏 | **先只做预览，确认后再执行** | 已实现：`preview` 只读；`apply` 按确认表执行，默认 move，不覆盖；未在真实媒体上运行 |

### 14.4 由真实数据带来的环境修正

| # | 原假设 | 实测 | 处理 |
| --- | --- | --- | --- |
| 18 | Python 3.12+、可装依赖 | 只有 **3.10.12，无 pip**，`venv` 也建不出 | 纯标准库；配置从 TOML 改 **JSON** |
| 19 | 默认 `chown jellyfin:jellyfin` | 库目录已有 **默认 ACL `user:jellyfin:rwx`** | **默认不 chown、不需要 root**；`--chown` 是阶段二可选项，非 root 只告警 |
| 20 | 可选 `copy` / `hardlink` | `/data` 只剩 **9.6 GB**（99% 满） | 默认且推荐 `move`；`copy`/`hardlink` 必须显式指定，copy 会先做磁盘空间预检 |
| 21 | 需要调 API 刷新 | Jellyfin `EnableRealtimeMonitor = true` | 刷新是阶段二可选项（有 Key 才调用），失败只告警，不影响迁移 |
| 22 | 字幕分隔符 `.`/`-`/`_` 都行 | 源码 `MediaFlagDelimiters = ['.']`，**只认点**；`.ttml/.dfxp` 不支持 | 字幕名统一用 `.` 分隔；不支持的格式标记出来 |
| 23 | TMDb 可补年份 | TMDb / TVDB / Bangumi **全部不可达**（服务器网络限制） | 联网补年份彻底放弃 |

### 14.4.1 后续增补决策（2026-11-19，第二轮补充）

| # | 问题 | 决策 | 对应实现 |
| --- | --- | --- | --- |
| 24 | 入库后如何追溯「原始名 → 新名」 | **在每个条目文件夹里留一份记录**：电影写 `movies/片名 (年份)/`，剧集写 `tvshows/剧名 (年份)/`（**不是** `Season 0X/`）；默认 `jf-ingest.md`，含原始结构 / 当前结构 / 改名对照 | FR-17；`apply` 时写入，`preview` 只显示计划位置与内容预览 |
| 25 | 预览报告放哪、怎么看 | **程序目录 `logs/`**（Windows/Linux 同名目录），UTF-8 Markdown 纯文本，SSH 上直接 `more` / `less` / `vi` 看 | `paths.reports = "./logs"`；已实现 |
| 26 | 季号怎么判定 | 都没线索才按 `Season 01`；**文件名明写 `第二季` / `第三季` / `2nd Season` / `Season 3` / `S03` 时按实际季号**，优先级：文件名 `SxxExx` > 文件名文字季号 > 目录名 | 已实现；只有「都没写」才标 `SEASON_ASSUMED_1` 并列入 §4 未定 |
| 27 | 半集（`17.5`）怎么处理 | **和普通集一样**：原数字保留成 `E17.5`，不改成 `E17`/`E18`，也不留给人工决定 | 已实现；`HALF_EPISODE` 不再作为问题标记 |
| 28 | §5 逐条目明细怎么分类 | 分三组并依次排列：**5.1 从未见过的问题（需人工确认）**、**5.2 需要人工干涉和处理**、**5.3 能够自动处理的**；前两组带红色 🔴 标记，编号沿用 `plan.tsv` | 已实现 |

### 14.5 由决策带来的已知影响 / 风险

1. **字幕语言信息不完整**（决策 12 + 13）：`.sc`/`.tc` 不改写，客户端「按语言自动选字幕」可能不准；Jellyfin 仍会正常加载，语言显示未知。
2. **年份缺失**（决策 11 + 无年份来源）：Jellyfin 靠片名匹配，重名片可能刮错；库内已有同名目录时复用其年份；必要时手动 Identify。
3. **多版本命名依赖分辨率/版本词**：两个版本都没写 `1080p`/`2160p` 时只能退化成 `v2`/`v3`。
4. **花絮依赖宿主目录**：孤立花絮不猜归属，需人工放回后再跑。
5. **手动执行**：没有自动触发，忘记跑就不会入库。
6. **26 个条目带「待确认」标记**（17 中 + 2 低 + 7 仅记录项交织）：属于正常，不是解析失败。
7. **`jf-ingest.md` 是「事后凭证」**（决策 24）：只记录本次真正搬了什么，不参与 Jellyfin 刮削；
   如果被手工删掉，历史里「原来叫什么」就找不回来了（`logs/` 里的同批 JSON 仍在）。
   它不含文件内容、不含 hash，删改都不会影响播放。

### 14.6 仍待后续补充（不阻塞）

- Jellyfin 的 `base_url` / `api_key`（阶段二启用刷新时填）；
- 阶段二是否解压字幕包（需要先装 `p7zip-full`）；
- 是否需要「确认表可编辑后 apply」的工作流（阶段二）。


## 15. 参考资料

- Jellyfin 官方文档 · Movies：<https://jellyfin.org/docs/general/server/media/movies>
- Jellyfin 官方文档 · TV Shows：<https://jellyfin.org/docs/general/server/media/shows>
- Jellyfin 官方文档 · 元数据提供者 ID 命名：<https://jellyfin.org/docs/general/server/metadata/identifiers>
- Jellyfin 官方文档 · 排除文件/目录：<https://jellyfin.org/docs/general/server/media/excluding-directory>
- Jellyfin 源码 · 外挂字幕匹配逻辑（`MediaInfoResolver.GetExternalFiles`）：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Providers/MediaInfo/MediaInfoResolver.cs>
- Jellyfin 源码 · 语言码表（`iso6392.txt`，含 `zho`/`chi`）：<https://github.com/jellyfin/jellyfin/blob/master/Emby.Server.Implementations/Localization/LocalizationManager.cs>
- 核对日期：2026-11-18

---

## 附录 A：原始需求（口述记录，保持原意）

> 我要写一个基于 python3.12 的 python 程序。该程序只在 Linux 下运行。
>
> 1. 在 Linux 下有一个 `/data/upload` 的目录，会有人手工把影视文件放到该目录中，等到传输完成后，就执行我们现在要写的这个程序，这个程序目的是为了让 Jellyfin 能够认识这些上传的影视文件。
> 2. 目前 `/data/jellyfin/movies` 是放电影文件，`/data/jellyfin/tvshows` 放剧集文件。
> 3. 电影文件是要有一个文件夹，文件夹下有电影的视频文件；一般情况视频文件与文件夹文件名相同（待确认）。
> 4. 剧集文件是有文件夹（文件夹名为剧集名），文件夹下有 `Season 01`、`Season 02` … 子文件夹，子文件夹中放具体视频文件；文件名规则必须要有 `SXXEYY`，其中 XX 为季（与上一级季文件夹名一致），YY 为集（如第 2 集为 `02`），如 `xxxxS01E02.后缀名`；后缀名不要改，mkv/avi 等文件本身是什么就是什么。
> 5. 如果上传的视频文件同目录下还有字幕文件，字幕文件也要随着视频文件名的修改一起修改。
> 6. 程序要可以执行预览，我能够有个地方看到哪些能够达到目的、并纳入 Jellyfin 的目录下；程序要有关键的日志。
