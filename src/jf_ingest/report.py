"""报告：Markdown（人读）、JSON（机器读）、TSV（确认表）。

约定：报告绝不写进 Jellyfin 库目录（movies / tvshows），否则会被当成媒体扫描。
"""

from __future__ import annotations

import collections
import dataclasses
import json
import re
from datetime import datetime
from pathlib import Path

from . import media
from .analyze import EntryPlan

SUMMARY_ORDER = ["movie", "tv", "unknown"]
KIND_LABEL = {"movie": "电影", "tv": "剧集", "unknown": "无法判断"}
CONF_LABEL = {"high": "高", "medium": "中", "low": "低"}

ISSUE_TEXT = dict(media.ISSUES)

# §3「必须人工干预」：前面已经决定程序不动的条目
MANUAL_CODES = {
    "NOT_MEDIA", "NO_VIDEO", "NO_EPISODE", "TITLE_LOW_CONFIDENCE", "DUP_RELEASE",
    "MULTI_PART",  # 分段电影只给第一个视频安排了目标，apply 不能把它拆散
}
MANUAL_ACTION = {
    "NOT_MEDIA": "跳过，不入库",
    "NO_VIDEO": "没有正片，跳过",
    "NO_EPISODE": "人工判断是分段还是多集，改名后重跑",
    "TITLE_LOW_CONFIDENCE": "先把片名改对，再重新跑预览",
    "DUP_RELEASE": "两套 release 重叠：按决策整个条目不动，人工挑一套",
    "MULTI_PART": "疑似同一部电影分段：人工确认后重跑，避免只搬走第一段",
}

def is_manual(plan: EntryPlan) -> bool:
    """§3「必须人工干预」判定；apply 也用它决定跳过哪些条目。"""
    return bool(set(plan.issues) & MANUAL_CODES) or plan.confidence == "low"


def detail_group(plan: EntryPlan) -> str:
    """§5 三分类：new / manual / auto（apply 只执行 auto）。"""
    unknown = set(plan.issues) - set(media.ISSUES)
    if plan.confidence == "low" or unknown:
        return "new"
    if is_manual(plan):
        return "manual"
    return "auto"


# TSV 里不能出现制表符/换行
_TSV_UNSAFE = re.compile(r"[\t\r\n]+")


def _tsv(value: object) -> str:
    return _TSV_UNSAFE.sub(" ", str(value)).strip()


def _fmt_ep(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def guard_report_dir(cfg: dict, path: Path) -> None:
    """报告/日志目录绝不能落在 Jellyfin 库目录里，否则会被当媒体扫描。"""
    for key in ("movies", "tvshows"):
        lib = Path(cfg["paths"][key])
        try:
            path.resolve().relative_to(lib.resolve())
            raise SystemExit(
                f"拒绝写入：报告目录 {path} 位于 Jellyfin 库目录 {lib} 内。"
                "请换一个目录（例如程序目录下的 logs/）。"
            )
        except ValueError:
            continue


class Reporter:
    def __init__(self, cfg: dict, source: str, scan_stats: dict, plans: list[EntryPlan]):
        self.cfg = cfg
        self.source = source
        self.scan = scan_stats
        self.plans = plans
        self.now = datetime.now()

    # ------------------------------------------------------------ 入口

    def write_all(self, report_dir: str | Path) -> dict[str, Path]:
        out = Path(report_dir)
        self._guard(out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = self.now.strftime("%Y%m%d-%H%M%S")
        paths = {
            "md": out / f"{stamp}-preview.md",
            "json": out / f"{stamp}-preview.json",
            "tsv": out / f"{stamp}-plan.tsv",
        }
        paths["md"].write_text(self._markdown(), encoding="utf-8")
        paths["json"].write_text(self._json(), encoding="utf-8")
        paths["tsv"].write_text(self._tsv(), encoding="utf-8")
        return paths

    def _guard(self, path: Path) -> None:
        guard_report_dir(self.cfg, path)

    # ------------------------------------------------------------ 统计

    def _stats(self) -> dict:
        by_kind = collections.Counter(p.kind for p in self.plans)
        by_conf = collections.Counter((p.kind, p.confidence) for p in self.plans)
        issue_count = collections.Counter()
        for plan in self.plans:
            for issue in plan.issues:
                issue_count[issue] += 1
        videos = sum(p.media_count for p in self.plans)
        return {
            "by_kind": by_kind,
            "by_conf": by_conf,
            "issue_count": issue_count,
            "videos": videos,
            "extras": sum(len(p.extras) for p in self.plans),
            "subs": sum(p.subtitle_count for p in self.plans),
        }

    # ------------------------------------------------------------ Markdown

    def _markdown(self) -> str:
        s = self._stats()
        lines: list[str] = []
        add = lines.append

        add("# jf-ingest 预览报告")
        add("")
        add("> **只读预览**：没有移动、改名或删除任何文件。所有「目标」都只是建议。")
        add("")
        add("| 项目 | 值 |")
        add("| --- | --- |")
        add(f"| 生成时间 | {self.now.strftime('%Y-%m-%d %H:%M:%S')} |")
        add(f"| 源目录 | `{self.source}` |")
        add(f"| 电影库 | `{self.cfg['paths']['movies']}` |")
        add(f"| 剧集库 | `{self.cfg['paths']['tvshows']}` |")
        size_text = media.human_size(self.scan.get("size", 0))
        add(f"| 扫描文件 | {self.scan.get('files', 0)} 个（{size_text}） |")
        add(f"| 顶层条目 | {len(self.plans)} |")
        add(
            f"| 忽略 | 隐藏 {self.scan.get('hidden', 0)} / 垃圾 {self.scan.get('junk', 0)}"
            f" / 空文件 {self.scan.get('empty', 0)} / 未稳定 {self.scan.get('unstable', 0)} |"
        )
        add("")

        add("## 1. 总览")
        add("")
        add("| 类型 | 条目 | 视频 | 高 | 中 | 低 |")
        add("| --- | ---: | ---: | ---: | ---: | ---: |")
        for kind in SUMMARY_ORDER:
            n = s["by_kind"].get(kind, 0)
            if not n:
                continue
            vids = sum(p.media_count for p in self.plans if p.kind == kind)
            add(
                f"| {KIND_LABEL[kind]} | {n} | {vids} | "
                f"{s['by_conf'].get((kind, 'high'), 0)} | "
                f"{s['by_conf'].get((kind, 'medium'), 0)} | "
                f"{s['by_conf'].get((kind, 'low'), 0)} |"
            )
        add("")
        add(f"共识别视频 {s['videos']} 个（另有花絮 {s['extras']} 个），"
            f"可直接配对的外挂字幕 {s['subs']} 份。")
        add("")

        add("## 2. 问题标记汇总")
        add("")
        add(
            "> 这里列出所有被程序标记的情况，**包含已决定不处理的**；"
            "哪些要人工、哪些未定见 §3 / §4。"
        )
        add("")
        if not s["issue_count"]:
            add("无。")
        else:
            add("| 问题（代码 / 中文） | 条数 | 含义 |")
            add("| --- | ---: | --- |")
            for code, count in s["issue_count"].most_common():
                add(f"| {media.issue_label(code)} | {count} | {ISSUE_TEXT.get(code, '')} |")
        add("")

        self._section_manual(lines)
        self._section_undecided(lines)
        self._section_entries(lines)
        self._section_ignored(lines)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _is_manual(plan: EntryPlan) -> bool:
        return is_manual(plan)

    @classmethod
    def _manual_reason(cls, plan: EntryPlan) -> tuple[str, str]:
        """§3 和 §5 共用同一句「原因 / 要做的事」，避免两处口径不一致。"""
        codes = [i for i in plan.issues if i in MANUAL_CODES]
        reasons = "、".join(media.issue_label(i) for i in codes) or "低置信度"
        actions = "；".join(
            dict.fromkeys(MANUAL_ACTION.get(i, "人工确认后再处理") for i in codes)
        )
        if not codes:
            actions = "人工确认后再处理"
        return reasons, actions

    def _section_manual(self, lines: list[str]) -> None:
        """前面已经决定「程序不动、必须人工处理」的条目。"""
        add = lines.append
        add("## 3. 必须人工干预")
        add("")
        add(
            "> 这些是前面已经决定要人处理的条目（低置信度 + 明确决定保留原样的重复 release）。"
            "程序不给它们安排目标路径，阶段二也不会动它们。"
        )
        add("")
        add("| # | 源目录 | 片名 | 原因 | 你要做的事 |")
        add("| ---: | --- | --- | --- | --- |")
        found = False
        for seq, plan in enumerate(self.plans, 1):
            if not self._is_manual(plan):
                continue
            found = True
            reasons, actions = self._manual_reason(plan)
            add(
                f"| {seq} | `{plan.root}` | {plan.title or '—'} | {reasons} | {actions} |"
            )
        if not found:
            add("| — | — | — | 无 | — |")
        add("")

    def _section_undecided(self, lines: list[str]) -> None:
        """暂时无法确认的细节；不同于「必须人工干预」，它们不阻塞入库。"""
        add = lines.append
        add("## 4. 未定")
        add("")
        add(
            "> 以下是程序**暂时无法确认**的细节。当前已按需求里的规则给出目标，"
            "不会因为这些标记停下来；如果你觉得判断不对，告诉我再调整。"
        )
        add("")
        seq_of = {id(plan): i for i, plan in enumerate(self.plans, 1)}
        groups = (
            (
                "4.1 季号未确认",
                "SEASON_ASSUMED_1",
                "目录和文件名里没有 `S02`/`第二季` 之类线索，暂按第 1 季入库；"
                "文件名或目录名里明写了季号的（如 `第二季`、`3rd Season`）会按实际季号处理。",
            ),
        )
        any_group = False
        for title, code, note in groups:
            # 「必须人工干预」的条目不重复列到「未定」里
            group = [p for p in self.plans if code in p.issues and not self._is_manual(p)]
            if not group:
                continue
            any_group = True
            add(f"### {title}（{media.issue_label(code)}，{len(group)} 条）")
            add("")
            add(f"> {note}")
            add("")
            add("| # | 源目录 | 片名 | 当前处理 |")
            add("| ---: | --- | --- | --- |")
            for plan in group:
                current = f"Season {plan.season or 1:02d} ｜ 集 {plan.ep_range or '—'}"
                add(
                    f"| {seq_of[id(plan)]} | `{plan.root}` | {plan.title or '—'} | {current} |"
                )
            add("")
        if not any_group:
            add("无。")
            add("")
        add(
            "> 已经决定、不再列为未定：`EP_RANGE_SUSPECT`（集号只按原数字排序，"
            "例如 04/05/06 → S01E04/05/06，不重编号）、半集（17.5 保留成 E17.5，"
            "和普通集一样入库）、`NO_YEAR`（不带年份）、"
            "字幕 `.sc`/`.tc`、压缩包和子目录里的字幕（只记录不处理）、"
            "重复 release（列在 §3，整个条目不动）。它们仍出现在 §2 汇总和 §5 明细里。"
        )
        add("")

    @classmethod
    def _detail_group(cls, plan: EntryPlan) -> str:
        """§5 的三分类：新问题 / 需要人工干涉和处理 / 能够自动处理。"""
        return detail_group(plan)

    def _section_entries(self, lines: list[str]) -> None:
        add = lines.append
        add("## 5. 逐条目明细")
        add("")
        add(
            "> 按处理方式分三类，条目编号沿用 §3 / §4 和 `plan.tsv` 里的序号。"
            "第一类是程序从没见过、需要你先确认规则的情况；"
            "第二类是已知规则但必须人来处理；第三类按当前规则自动入库。"
        )
        add("")
        groups = (
            ("new", "5.1 从未见过的问题（需人工确认）"),
            ("manual", "5.2 需要人工干涉和处理"),
            ("auto", "5.3 能够自动处理的"),
        )
        seq_of = {id(plan): i for i, plan in enumerate(self.plans, 1)}
        for key, title in groups:
            members = [(seq_of[id(p)], p) for p in self.plans if self._detail_group(p) == key]
            if not members:
                continue
            add(f"### {title}（{len(members)} 条）")
            add("")
            for seq, plan in members:
                self._entry_detail(lines, seq, plan, key)
        add("")

    def _entry_detail(self, lines: list[str], seq: int, plan: EntryPlan, group: str) -> None:
        add = lines.append
        add(f"#### {seq}. {KIND_LABEL[plan.kind]} ｜ {plan.confidence.upper()} ｜ `{plan.root}`")
        add("")
        if group in {"new", "manual"}:
            reasons, actions = self._manual_reason(plan)
            label = (
                "【从未见过的问题，需人工确认】" if group == "new"
                else "【需要人工干涉和处理】"
            )
            # Markdown 本身没有颜色；支持行内 HTML 的阅读器会显示红色。
            # vi / more / less 下仍能看到 🔴 和中文标记。
            add(
                f'<span style="color:red">🔴 **{label}**（见 §3）：'
                f'{reasons}；{actions}</span>'
            )
            add("")
        head = []
        if plan.title:
            head.append(f"**片名**：{plan.title}")
        head.append(f"**年份**：{plan.year or '（无）'}")
        if plan.kind == "tv":
            head.append(f"**季**：{plan.season or 1}")
            head.append(f"**集**：{plan.ep_range or '—'}")
        add(" ｜ ".join(head))
        add("")
        add(
            f"- 大小：{media.human_size(plan.total_size)}"
            f" ｜ 正片：{plan.media_count}"
            f" ｜ 花絮：{len(plan.extras)}"
            f" ｜ 字幕：{plan.subtitle_count}"
        )
        if plan.movie_target:
            add(f"- 目标：`{plan.movie_target}`")
        if plan.episodes:
            self._episode_table(lines, plan)
        if plan.subs:
            self._sub_table(lines, plan)
        if plan.extras:
            kinds = collections.Counter(e.kind for e in plan.extras)
            detail = "、".join(f"{k}×{v}" for k, v in kinds.most_common())
            add(f"- 花絮：{detail}（阶段一不搬运）")
        if plan.issues:
            add("- 待确认：" + "、".join(media.issue_label(i) for i in plan.issues))
        for note in plan.notes:
            add(f"- 备注：{note}")
        add("")

    def _episode_table(self, lines: list[str], plan: EntryPlan) -> None:
        limit = int(self.cfg.get("behavior", {}).get("max_episodes_in_md", 5))
        eps = plan.episodes
        shown = eps if len(eps) <= limit else eps[: limit - 1] + eps[-1:]
        add = lines.append
        add("")
        add("| 源文件 | 目标 |")
        add("| --- | --- |")
        for idx, ep in enumerate(shown):
            if len(eps) > limit and idx == len(shown) - 1:
                add(f"| … | 共 {len(eps)} 集，完整列表见 JSON |")
            target = ep.target or "—"
            add(f"| `{ep.source}` | `{target}` |")
        add("")

    def _sub_table(self, lines: list[str], plan: EntryPlan) -> None:
        add = lines.append
        grouped = collections.Counter(sub.status for sub in plan.subs)
        parts = []
        if grouped.get("same_dir"):
            parts.append(f"同目录配对 {grouped['same_dir']}")
        if grouped.get("subdir"):
            parts.append(f"子目录待搬 {grouped['subdir']}")
        if grouped.get("unpaired"):
            parts.append(f"配对失败 {grouped['unpaired']}")
        if grouped.get("unsupported"):
            parts.append(f"格式不支持 {grouped['unsupported']}")
        if grouped.get("archive"):
            parts.append(f"压缩包内 {grouped['archive']}")
        add("- 字幕：" + "，".join(parts))
        for sub in plan.subs:
            if sub.status == "same_dir" and sub.target:
                tag = ".".join(sub.tags) if sub.tags else "（无标签）"
                add(f"  - `{sub.source}` → `{sub.target}`（标签 {tag}）")
            elif sub.status == "subdir":
                add(f"  - ⚠ 子目录：`{sub.source}`（建议搬到 `{sub.target}`）")
            elif sub.status == "archive":
                add(f"  - ⚠ 压缩包：`{sub.source}`（Jellyfin 看不到，需解压）")
            elif sub.status == "unsupported":
                add(f"  - ⚠ 格式不支持：`{sub.source}`")
            elif sub.status == "unpaired":
                add(f"  - ⚠ 配不上视频：`{sub.source}`")

    def _section_ignored(self, lines: list[str]) -> None:
        add = lines.append
        add("## 6. 扫描时跳过的内容")
        add("")
        add("| 类别 | 数量 | 说明 |")
        add("| --- | ---: | --- |")
        add(f"| 隐藏文件/目录 | {self.scan.get('hidden', 0)} | `.` 开头 |")
        add(f"| 垃圾/无关文件 | {self.scan.get('junk', 0)} | 种子、临时文件、exe、docx 等 |")
        add(f"| 空文件 | {self.scan.get('empty', 0)} | 0 字节 |")
        add(f"| 未稳定文件 | {self.scan.get('unstable', 0)} | 最近仍在变化 |")
        add("")

    # ------------------------------------------------------------ JSON / TSV

    def _json(self) -> str:
        payload = {
            "generated_at": self.now.isoformat(timespec="seconds"),
            "mode": "preview",
            "source": self.source,
            "libraries": {
                "movies": self.cfg["paths"]["movies"],
                "tvshows": self.cfg["paths"]["tvshows"],
            },
            "scan": self.scan,
            "entries": [self._plan_dict(p, i) for i, p in enumerate(self.plans, 1)],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _plan_dict(plan: EntryPlan, seq: int) -> dict:
        data = dataclasses.asdict(plan)
        data["seq"] = seq
        return data

    def _tsv(self) -> str:
        rows = [
            "序号\t类型\t置信度\t源目录\t片名\t年份\t季\t集范围\t正片\t花絮\t字幕\t目标\t待确认"
        ]
        for seq, plan in enumerate(self.plans, 1):
            if plan.movie_target:
                target = plan.movie_target
            else:
                dirs = [ep.target.rsplit("/", 1)[0] for ep in plan.episodes if ep.target]
                target = dirs[0] if dirs else ""
            rows.append(
                "\t".join(
                    _tsv(v)
                    for v in (
                        seq,
                        plan.kind,
                        plan.confidence,
                        plan.root,
                        plan.title,
                        plan.year or "",
                        plan.season or "",
                        plan.ep_range,
                        plan.media_count,
                        len(plan.extras),
                        plan.subtitle_count,
                        target,
                        ",".join(plan.issues),
                    )
                )
            )
        return "\n".join(rows) + "\n"
