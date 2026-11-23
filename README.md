# jf-ingest

把 `/data/jellyfin/uploads` 里手工投放的影视文件，整理成 Jellyfin 能正确识别的结构：

- 电影 → `/data/jellyfin/movies`
- 剧集 → `/data/jellyfin/tvshows`

> **当前阶段：只读预览（v0.3）**
> 程序只扫描 + 识别 + 生成报告，**不移动、不改名、不改属主**。
> 执行（apply）要等你看完真实预览后再决定。

## 它现在能做什么

给定 203 个条目、1297 个视频、896 GB 的真实上传目录，输出三份文件：

| 文件 | 用途 |
| --- | --- |
| `logs/<时间>-preview.md` | 给人看：总览、必须人工干预、未定、每个条目的「源 → 目标」 |
| `logs/<时间>-plan.tsv` | 一行一个条目的确认表（以后按它执行） |
| `logs/<时间>-preview.json` | 机器可读：每一集、每一份字幕、每个问题码 |

实测结果（2026-11-19）：

| 类型 | 条目 | 视频 | 高可信 | 中 | 低 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 电影 | 119 | 119 | 119 | 0 | 0 |
| 剧集 | 82 | 1072 | 65 | 17 | 0 |
| 无法判断 | 2 | 5 | 0 | 0 | 2 |

扫描耗时 **0.74 秒**（1984 个文件 / 896 GB，只读元数据）。

## 运行环境（实测，别按想象来）

| 项 | 实际情况 |
| --- | --- |
| Python | **3.10.12**（系统自带） |
| 第三方依赖 | **没有 pip，也建不出 venv** → 程序是**纯标准库** |
| 配置格式 | **JSON**（3.10 没有 `tomllib`，也装不了 `tomli`） |
| 磁盘 | `/data` 已用 99%，剩 9.6 GB → 将来只做 `move`，不做 `copy` |
| 属主 | 库目录已有默认 ACL `user:jellyfin:rwx` → **不需要 chown、不需要 root** |
| 外部命令 | `unzip` 有；`7z` / `ffprobe` / `mediainfo` 都没有 |

## 部署与运行

```bash
# 1. 把项目拷到服务器（任选目录，示例用家目录）
scp -r src bin config.example.json luna@192.168.3.87:~/jf-ingest/

# 2. 跑预览（只读）
cd ~/jf-ingest
chmod +x bin/jf-ingest
./bin/jf-ingest preview --config config.example.json

# 报告默认写到 ./logs/，也可以指定
./bin/jf-ingest preview --report-dir /tmp/jf-report
```

也可以直接用模块方式（等价）：

```bash
PYTHONPATH=src python3 -m jf_ingest preview --config config.example.json
```

### 常用参数

```text
--source PATH            源目录（默认 /data/jellyfin/uploads）
--report-dir PATH        报告目录（默认 ./logs）
--only 3,17              只分析指定序号（配合报告里的编号）
--limit 20               只分析前 20 个条目
--from-listing FILE      从清单 TSV 离线分析，不读磁盘
--map-subtitle-language  字幕标签映射成 zh-CN/zh-TW（默认关，只记录）
--max-episodes 10        Markdown 里每条目最多展示几集
```

## 报告怎么读

- **§1 总览**：条目/视频/置信度统计；
- **§2 问题标记汇总**：每个问题码有多少条、什么意思（含已决定不处理的）；
- **§3 必须人工干预**：前面已决定程序不动的条目（游戏包、无编号合集、重复 release），阶段二会跳过；
- **§4 未定**：程序暂时无法确认的细节——只有「未标季号、暂按第 1 季」；文件名明写了季号的会按实际季号处理，不再算未定；集号原样排序、半集 E17.5 都已确认，只在说明里提一句；
- **§5 逐条目明细**：按处理方式分三组并按此顺序排列——**5.1 从未见过的问题（需人工确认）** → **5.2 需要人工干涉和处理** → **5.3 能够自动处理的**；每条目仍给出「源 → 目标」、集映射和字幕建议名，编号沿用 `plan.tsv`；前两组带红色 🔴 标记；
- **§6 扫描时跳过的内容**：隐藏文件、种子/临时文件等。

> Markdown 本身没有「颜色」语法，红色用行内 HTML `<span style="color:red">` 实现：支持 HTML 的阅读器（Typora / VS Code / 大部分网页 Markdown）会显示红色；SSH 里用 `vi` / `more` / `less` 看纯文本时，看到的是 🔴 和中文分类标记（`【从未见过的问题，需人工确认】` / `【需要人工干涉和处理】`），效果一样醒目。

Jellyfin 命名规则已按官方文档与源码核对过：
`movies/片名 (年份)/片名 (年份).mkv`、`tvshows/剧名 (年份)/Season 01/剧名 (年份) S01E01.mkv`，
字幕只认「视频主名 + `.` + 标签」，且只在视频同目录查找。

## 开发

```powershell
# Windows 开发机：用 3.13 跑测试可以，但代码必须保持 3.10 兼容
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

目录结构：

```text
src/jf_ingest/
├── cli.py          # 命令行（preview）
├── config.py       # JSON 配置
├── scan.py         # 扫描 / 清单读取 / 条目分组
├── media.py        # 扩展名、数据模型、问题码
├── textutil.py     # 片名清洗、年份、季号
├── detect.py       # 电影/剧集判定、集号、花絮
├── subtitles.py    # 字幕配对（同目录 / 子目录 / 压缩包）
├── naming.py       # 目标路径生成
├── analyze.py      # 组装条目计划
└── report.py       # MD / JSON / TSV 报告
tests/              # 67 个测试，覆盖真实文件名
tools/              # 一次性分析脚本（服务器清点、试算、统计）
bin/jf-ingest       # shell 包装
doc/requirements.md # 需求文档（v0.3）
doc/inventory/      # 真实数据清点与分析
```

## 文档

- 需求文档：[`doc/requirements.md`](doc/requirements.md)（v0.3，含决策记录 §14）
- 真实数据分析：[`doc/inventory/upload-analysis.md`](doc/inventory/upload-analysis.md)
- 真实清单：[`doc/inventory/upload-files.tsv`](doc/inventory/upload-files.tsv)
- 真实预览报告：`doc/inventory/real-run/`

## 已知限制（阶段一）

- 不移动、不改名、不 chown；
- **`jf-ingest.md` 入库记录还没写**（阶段二 `apply` 才写；需求见 `doc/requirements.md` 的 FR-17）；
- 压缩包里的字幕不解压（24 个包，服务器没有 7z）；
- 只有目录名和文件名都没有季号线索时才按 `Season 01`；文件名写了 `第二季` / `3rd Season` 会按实际季号；
- 连续编号（如 48.5–72）不做偏移换算，集号（含半集 `E17.5`）一律保留原数字；
- 无编号合集、游戏安装包等留在 `uploads`，只在报告里列出。
