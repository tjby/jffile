"""读取 apply 的确认表：plan.tsv 选条目，preview.json 提供逐文件明细。

plan.tsv 只有「一条一行」的摘要，真正的源→目标映射在同批次 preview.json 里
（两者文件名时间戳相同）。找不到 JSON 时回退为重新扫描，再按序号/源目录核对。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import analyze, scan
from .logutil import EventLogger

REQUIRED_COLUMNS = {"序号", "类型", "源目录", "目标"}


class PlanError(ValueError):
    """确认表缺失、损坏或与预览数据不一致。"""


@dataclass
class ApplyPlan:
    entries: list[analyze.EntryPlan] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    # 预览里的原始序号（源目录 → 序号）；TSV 可能只勾选了一部分，枚举下标不能当序号
    seq_by_root: dict[str, int] = field(default_factory=dict)
    origin: str = ""
    warnings: list[str] = field(default_factory=list)


def read_confirmation(path: str | Path) -> list[dict[str, str]]:
    p = Path(path)
    if not p.is_file():
        raise PlanError(f"确认表不存在: {p}")
    try:
        with p.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            rows = [dict(row) for row in reader]
    except (OSError, csv.Error) as exc:
        raise PlanError(f"无法读取确认表 {p}: {exc}") from exc
    if not rows:
        raise PlanError(f"确认表是空的: {p}")
    missing = REQUIRED_COLUMNS - set(rows[0])
    if missing:
        raise PlanError(f"确认表缺少列：{', '.join(sorted(missing))}")
    for row in rows:
        raw = (row.get("序号") or "").strip()
        if not raw.isdigit():
            raise PlanError(f"确认表里有非法序号: {raw!r}")
        row["序号"] = raw
    return rows


def _entry_from_dict(data: dict[str, Any]) -> analyze.EntryPlan:
    if data.get("movie_target") and not data.get("movie_source"):
        raise PlanError(
            f"条目 {data.get('root')!r} 的预览数据缺少 movie_source（旧版 preview.json）；"
            "请重新运行 preview 生成确认表和预览数据"
        )
    try:
        return analyze.EntryPlan(
            root=data["root"],
            kind=data["kind"],
            title=data["title"],
            year=data.get("year"),
            season=data.get("season"),
            confidence=data["confidence"],
            issues=list(data.get("issues", [])),
            notes=list(data.get("notes", [])),
            episodes=[analyze.EpisodePlan(**e) for e in data.get("episodes", [])],
            movie_target=data.get("movie_target"),
            movie_source=data.get("movie_source"),
            subs=[analyze.SubPlan(**s) for s in data.get("subs", [])],
            extras=[analyze.ExtraPlan(**e) for e in data.get("extras", [])],
            media_count=int(data.get("media_count", 0)),
            total_size=int(data.get("total_size", 0)),
            source_files=list(data.get("source_files", [])),
        )
    except (KeyError, TypeError) as exc:
        raise PlanError(f"preview.json 里的条目结构不完整: {exc}") from exc


def preview_json_path(plan_tsv: str | Path) -> Path:
    p = Path(plan_tsv)
    name = p.name
    if name.endswith("-plan.tsv"):
        name = name[: -len("-plan.tsv")] + "-preview.json"
    else:
        name = p.stem + "-preview.json"
    return p.with_name(name)


def _load_json(path: Path, allowed: dict[int, str], logger: EventLogger) -> list[analyze.EntryPlan]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_entries = payload["entries"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PlanError(f"无法读取预览数据 {path}: {exc}") from exc

    selected: list[analyze.EntryPlan] = []
    for raw in raw_entries:
        seq = int(raw.get("seq", 0))
        if seq not in allowed:
            continue
        plan = _entry_from_dict(raw)
        if allowed[seq] != plan.root:
            raise PlanError(
                f"确认表第 {seq} 行的源目录是 {allowed[seq]!r}，"
                f"预览数据里却是 {plan.root!r}；请重新生成预览"
            )
        selected.append(plan)
    if not selected:
        raise PlanError("确认表里的序号在 preview.json 里一个都找不到；请重新生成预览")
    logger.event("PLAN_LOADED", "已从 preview.json 读取逐文件计划", file=str(path),
                 entries=len(selected))
    return selected


def _rescan(cfg: dict[str, Any], allowed: dict[int, str], listing: str | None,
            logger: EventLogger) -> list[analyze.EntryPlan]:
    if listing:
        result = scan.load_listing(listing)
    else:
        result = scan.scan_filesystem(
            cfg["paths"]["source"],
            stable_seconds=float(cfg["behavior"].get("stable_seconds", 0)),
            follow_symlinks=bool(cfg["behavior"].get("follow_symlinks", False)),
        )
    for message in result.errors:
        logger.warning("SCAN_WARN", message)
    entries = scan.build_entries(result.files)
    plans = [analyze.build(entry, cfg) for entry in entries]
    selected = []
    for seq, plan in enumerate(plans, 1):
        if seq not in allowed:
            continue
        if allowed[seq] != plan.root:
            raise PlanError(
                f"重新扫描后第 {seq} 行的源目录变成 {plan.root!r}（确认表是 {allowed[seq]!r}）；"
                "源目录已变化，请重新生成预览"
            )
        selected.append(plan)
    if not selected:
        raise PlanError("按确认表重新扫描后没有匹配到任何条目")
    logger.event("PLAN_RESCANNED", "未找到 preview.json，已按确认表重新扫描",
                 entries=len(selected))
    return selected


def load(plan_tsv: str | Path, cfg: dict[str, Any], logger: EventLogger,
         listing: str | None = None) -> ApplyPlan:
    p = Path(plan_tsv).expanduser()
    rows = read_confirmation(p)
    allowed = {int(row["序号"]): (row.get("源目录") or "").strip() for row in rows}

    result = ApplyPlan(rows=rows, seq_by_root={v: k for k, v in allowed.items()})
    jpath = preview_json_path(p)
    if jpath.is_file():
        result.entries = _load_json(jpath, allowed, logger)
        result.origin = str(jpath)
    else:
        result.warnings.append(f"没找到 {jpath.name}，改为按确认表重新扫描源目录")
        logger.warning("PLAN_FALLBACK", result.warnings[-1])
        result.entries = _rescan(cfg, allowed, listing, logger)
        result.origin = "rescan"
    return result
