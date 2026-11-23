# jf-ingest

把 `/data/jellyfin/uploads` 里手工投放的影视文件，整理成 Jellyfin 能正确识别的结构：

- 电影 → `/data/jellyfin/movies/片名 (年份)/片名 (年份).ext`
- 剧集 → `/data/jellyfin/tvshows/剧名 (年份)/Season NN/剧名 (年份) SxxEyy.ext`

> **工作流：先 `preview`（只读），确认报告后再 `apply`。**
> `apply` 默认只 `move`、不覆盖、不动报告里 §3/§5.1/§5.2 的条目；没有授权不要拿真实媒体试跑。

## 运行环境（实测，别按想象来）

| 项 | 实际情况 |
| --- | --- |
| Python | **3.10.12**（服务器系统自带） |
| 第三方依赖 | **没有 pip，也建不出 venv** → 程序是**纯标准库** |
| 配置格式 | **JSON**（3.10 没有 `tomllib`） |
| 磁盘 | `/data` 已用 99%，剩 9.6 GB → 默认只做 `move`，不做 `copy` |
| 属主 | 库目录已有默认 ACL `user:jellyfin:rwx` → **不需要 chown、不需要 root** |
| 外部命令 | `unzip` 有；`7z` / `ffprobe` / `mediainfo` 都没有 |

## 部署与运行

```bash
# 1. 把项目拷到服务器（任选目录，示例用家目录）
scp -r src bin config.example.json luna@192.168.3.87:~/jf-ingest/

# 2. 只读预览
cd ~/jf-ingest
chmod +x bin/jf-ingest
./bin/jf-ingest preview --config config.example.json

# 3. 看 logs/<时间>-preview.md 和 logs/<时间>-plan.tsv，
#    确认没问题后再执行（默认 move）：
./bin/jf-ingest apply --config config.example.json \
  --plan logs/<时间>-plan.tsv
```

等价的模块方式：

```bash
PYTHONPATH=src python3 -m jf_ingest preview --config config.example.json
PYTHONPATH=src python3 -m jf_ingest apply --config config.example.json --plan <确认表.tsv>
```

### preview 参数

```text
--source PATH            源目录（默认 /data/jellyfin/uploads）
--report-dir PATH        报告目录（默认 ./logs）
--only 3,17              只分析指定序号（配合报告里的编号）
--limit 20               只分析前 20 个条目
--from-listing FILE      从清单 TSV 离线分析，不读磁盘
--map-subtitle-language  字幕标签映射成 zh-CN/zh-TW（默认关，只记录）
--max-episodes 10        Markdown 里每条目最多展示几集
```

### apply 参数

```text
--plan FILE              必填：preview 生成的 *-plan.tsv（确认表，决定执行哪些条目）
--only 3,17              只执行这些原始序号
--mode move|copy|hardlink   落盘策略（默认 move；磁盘紧张别用 copy）
--on-conflict skip|suffix   目标已存在且内容不同时：跳过（默认）/ 用版本名或 (2) 后缀
--prune-empty-dirs       处理完删除已空的源目录
--chown USER:GROUP       设置属主（需 root，非 root 只告警跳过）
--no-chown               明确不改属主（默认）
--refresh / --no-refresh 是否调用 Jellyfin 刷新（默认按配置；没 api_key 只跳过）
--no-manifest            不写 jf-ingest.md 入库记录
--manifest-name NAME     入库记录文件名（不含扩展名，默认 jf-ingest）
--manifest-format md|txt 入库记录格式（默认 md）
```

`apply` 从同时间戳的 `preview.json` 里读逐文件映射；找不到时会按确认表重新扫描源目录，并核对序号/源目录，不一致就报错退出（不会瞎搬）。

## 报告怎么看

preview 写三份文件（默认 `logs/`）：

| 文件 | 用途 |
| --- | --- |
| `<时间>-preview.md` | 给人看：总览、问题汇总、必须人工干预、未定、逐条目明细 |
| `<时间>-plan.tsv` | 一行一个条目的确认表，勾选/删行后交给 `apply --plan` |
| `<时间>-preview.json` | 机器可读：每一集、每份字幕、每个文件的源→目标映射 |

Markdown 章节顺序固定：§1 总览 / §2 问题标记汇总 / **§3 必须人工干预** / **§4 未定** / §5 逐条目明细（**5.1 从未见过的问题** → **5.2 需要人工干涉和处理** → **5.3 能够自动处理**）/ §6 扫描时跳过的内容。§3 与 §4 不重复列条目；前两组在 §5 里带 🔴 和中文标记，SSH 纯文本下也醒目。

apply 写两份文件：

| 文件 | 用途 |
| --- | --- |
| `<时间>-apply.md` | 执行结果：条目/文件统计、逐文件结果、Jellyfin 刷新、注意事项 |
| `<时间>-apply.json` | 同样的内容，机器可读 |

运行日志统一追加到 `logs/jf-ingest.log`（滚动 10 MiB × 5），每行带 `run_id | 事件码 | 关键字段`，例如 `MOVE_OK`、`CONFLICT`、`MANIFEST_OK`、`REFRESH_FAILED`；API Key 永不写明文。

## 入库记录（FR-17）

`apply` 成功后会在电影文件夹 / 剧集文件夹**根目录**（不是 `Season 0X/`）写 `jf-ingest.md`，固定四段：

1. 摘要（类型、年份、源目录、首次建档/最后更新、结果）
2. 原始结构（树）
3. 当前结构（树）
4. 改名对照（`原始 -- 当前`，Markdown 里是表格）

路径全部 POSIX 相对路径，库搬家后仍旧可读。再次执行会**增量合并**：保留「首次建档」和旧记录，按当前路径去重更新；遇到同名但不是本程序写的清单，绝不覆盖，另存 `jf-ingest.<时间戳>.md` 并记 `MANIFEST_CONFLICT`。

## 安全规则

- 只处理报告 §5.3「能够自动处理」的条目；§3、§5.1、§5.2 一律跳过。
- 源必须在 `paths.source` 内，目标必须在 `movies/`/`tvshows/` 下且解析后仍在库目录内；`..` 一律拒绝。
- **绝不覆盖**：目标已存在且内容不同 → 默认 `CONFLICT`（源和原目标都保留）；`--on-conflict suffix` 才尝试 `片名 - 2160p.ext`、`片名 (2).ext`。
- 目标内容与源完全一致 → `SKIP_SAME`，幂等重跑不会产生新文件。
- 字幕跟着视频的目标主名走，扩展名不改；VobSub `.idx`/`.sub` 成对；花絮/封面/主题曲放条目目录或 `extras/`、`trailers/` 等子目录。
- 目录 `0755`、文件 `0644`；非 root 的 `--chown` 只告警不影响迁移。
- Jellyfin 刷新失败（含没配 Key）只告警，不影响迁移结果；`JF_API_KEY` 可代替配置里的 `jellyfin.api_key`。

退出码：`0` 全部成功 / `1` 部分失败或冲突 / `2` 参数、配置、确认表错误 / `3` 没有条目。

## 开发

```powershell
# Windows 开发机：用 3.13 跑测试可以，但代码必须保持 3.10 兼容
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

目录结构：

```text
src/jf_ingest/
├── cli.py          # 命令行（preview / apply）
├── config.py       # JSON 配置
├── scan.py         # 扫描 / 清单读取 / 条目分组
├── media.py        # 扩展名、数据模型、问题码
├── textutil.py     # 片名清洗、年份、季号
├── detect.py       # 电影/剧集判定、集号、花絮
├── subtitles.py    # 字幕配对（同目录 / 子目录 / 压缩包）
├── naming.py       # 目标路径生成
├── analyze.py      # 组装条目计划
├── report.py       # preview 的 MD / JSON / TSV 报告
├── applyplan.py    # 确认表 + preview.json 读取
├── executor.py     # 落盘、冲突、幂等、权限
├── manifest.py     # FR-17 入库记录
├── jellyfin.py     # Jellyfin 刷新（stdlib urllib）
├── logutil.py      # 日志
├── applyreport.py  # apply 的 MD / JSON 报告
└── apply.py        # apply 编排
tests/              # 覆盖真实文件名、落盘、合并、刷新、退出码
tools/              # 一次性分析脚本（服务器清点、试算、统计）
bin/jf-ingest       # shell 包装
doc/requirements.md # 需求文档（含决策记录 §14）
doc/inventory/      # 真实数据清点与分析
```

## 已知限制

- 压缩包里的字幕不解压（服务器没有 7z）；
- `.sc`/`.tc` 等特殊字幕、字幕包只记录不改名；
- 连续编号（如 48.5–72）不做偏移换算，集号（含半集 `E17.5`）一律保留原数字；
- 无编号合集、游戏安装包等留在 `uploads`，只在报告里列出；
- 编辑 `plan.tsv` 只能增删行（决定执行哪些条目）；逐文件目标以同批次 `preview.json` 为准，不能靠改 TSV 的「目标」列改落点。

## 文档

- 需求文档：[`doc/requirements.md`](doc/requirements.md)
- 真实数据分析：[`doc/inventory/upload-analysis.md`](doc/inventory/upload-analysis.md)
- 真实清单：[`doc/inventory/upload-files.tsv`](doc/inventory/upload-files.tsv)
- 真实预览报告：`doc/inventory/real-run/`
