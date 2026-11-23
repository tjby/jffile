"""apply 报告：apply-<时间>.md（人读）+ apply-<时间>.json（机器读）。"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from . import media, report
from .applyplan import ApplyPlan
from .executor import EntryResult
from .jellyfin import RefreshResult

STATUS_LABEL = {
    "success": "成功", "partial": "部分失败", "failed": "失败", "skipped": "跳过",
}
FILE_LABEL = {
    "OK": "已落盘", "VERSION_ADDED": "新增版本", "SKIP_SAME": "已存在且一致（跳过）",
    "CONFLICT": "冲突未覆盖", "SKIPPED": "跳过", "ERROR": "失败",
}


class ApplyReporter:
    def __init__(
        self,
        cfg: dict[str, Any],
        run_id: str,
        started: datetime,
        plan: ApplyPlan,
        results: list[EntryResult],
        refresh: RefreshResult,
        finished: datetime | None = None,
    ) -> None:
        self.cfg = cfg
        self.run_id = run_id
        self.started = started
        self.finished = finished or datetime.now()
        self.plan = plan
        self.results = results
        self.refresh = refresh

    def write_all(self, report_dir: str | Path) -> dict[str, Path]:
        out = Path(report_dir)
        report.guard_report_dir(self.cfg, out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = self.started.strftime("%Y%m%d-%H%M%S")
        paths = {"md": out / f"{stamp}-apply.md", "json": out / f"{stamp}-apply.json"}
        paths["md"].write_text(self._markdown(), encoding="utf-8", newline="\n")
        paths["json"].write_text(self._json(), encoding="utf-8", newline="\n")
        return paths

    # ------------------------------------------------------------ 统计

    def _stats(self) -> dict[str, Any]:
        by_entry = Counter(r.status for r in self.results)
        by_file = Counter(f.status for r in self.results for f in r.files)
        moved = sum(f.size for r in self.results for f in r.files
                    if f.status in {"OK", "VERSION_ADDED"})
        return {
            "entries": dict(by_entry),
            "files": dict(by_file),
            "moved_bytes": moved,
            "changed": sum(r.changed for r in self.results),
        }

    def _payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started.isoformat(timespec="seconds"),
            "finished_at": self.finished.isoformat(timespec="seconds"),
            "mode": self.cfg.get("behavior", {}).get("mode", "move"),
            "on_conflict": self.cfg.get("behavior", {}).get("on_conflict", "skip"),
            "source": self.cfg["paths"]["source"],
            "libraries": {
                "movies": self.cfg["paths"]["movies"],
                "tvshows": self.cfg["paths"]["tvshows"],
            },
            "plan_origin": self.plan.origin,
            "warnings": self.plan.warnings,
            "stats": self._stats(),
            "refresh": dataclasses.asdict(self.refresh),
            "entries": [dataclasses.asdict(r) for r in self.results],
        }

    def _json(self) -> str:
        return json.dumps(self._payload(), ensure_ascii=False, indent=2)

    # ------------------------------------------------------------ Markdown

    def _markdown(self) -> str:
        s = self._stats()
        lines: list[str] = []
        add = lines.append
        add("# jf-ingest 执行报告")
        add("")
        add(f"> run_id：`{self.run_id}`")
        add("")
        add("| 项目 | 值 |")
        add("| --- | --- |")
        start = self.started.strftime("%Y-%m-%d %H:%M:%S")
        end = self.finished.strftime("%Y-%m-%d %H:%M:%S")
        add(f"| 开始 / 结束 | {start} / {end} |")
        add(f"| 策略 | {self.cfg.get('behavior', {}).get('mode', 'move')} ｜ "
            f"冲突 {self.cfg.get('behavior', {}).get('on_conflict', 'skip')} |")
        add(f"| 源目录 | `{self.cfg['paths']['source']}` |")
        add(f"| 计划来源 | `{self.plan.origin}` |")
        add("")
        add("## 1. 结果总览")
        add("")
        add("| 条目状态 | 数量 |")
        add("| --- | ---: |")
        for key in ("success", "partial", "failed", "skipped"):
            add(f"| {STATUS_LABEL[key]} | {s['entries'].get(key, 0)} |")
        add("")
        add("| 文件状态 | 数量 |")
        add("| --- | ---: |")
        for key in ("OK", "VERSION_ADDED", "SKIP_SAME", "CONFLICT", "SKIPPED", "ERROR"):
            if s["files"].get(key):
                add(f"| {FILE_LABEL[key]}（{key}） | {s['files'][key]} |")
        add("")
        add(f"共实际落盘 {s['changed']} 个文件，"
            f"大小 {media.human_size(s['moved_bytes'])}。")
        add("")

        add("## 2. 逐条目结果")
        add("")
        for r in self.results:
            add(f"### {r.seq}. {media.KIND_LABEL_ZH.get(r.kind, r.kind)} ｜ "
                f"{STATUS_LABEL[r.status]} ｜ `{r.root}`")
            add("")
            if r.target_dirs:
                add("- 目标目录：" + "、".join(f"`{d}`" for d in r.target_dirs))
            for note in r.notes:
                add(f"- 备注：{note}")
            if r.manifest_file:
                add(f"- 入库记录：`{r.manifest_file}`"
                    + (f"（{r.manifest_code}）" if r.manifest_code != "OK" else ""))
            if r.files:
                add("")
                add("| 源文件 | 目标 | 结果 |")
                add("| --- | --- | --- |")
                for f in r.files:
                    target = f.actual or f.target or "—"
                    state = FILE_LABEL.get(f.status, f.status)
                    if f.reason:
                        state += f"：{f.reason}"
                    add(f"| `{f.source}` | `{target}` | {state} |")
            add("")

        add("## 3. Jellyfin 刷新")
        add("")
        add(f"- 模式：{self.refresh.mode}")
        add(f"- 结果：{self.refresh.label}")
        add(f"- 路径数：{len(self.refresh.paths)}")
        add("")

        if self.plan.warnings:
            add("## 4. 注意事项")
            add("")
            for warning in self.plan.warnings:
                add(f"- {warning}")
            add("")
        return "\n".join(lines) + "\n"


__all__ = ["ApplyReporter"]
