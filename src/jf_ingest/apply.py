"""apply 子命令编排：读确认表 → 校验 → 落盘 → 写清单 → 刷新 → 写报告。"""

from __future__ import annotations

import contextlib
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import applyplan, applyreport, config, jellyfin, manifest, media, report
from .applyplan import ApplyPlan, PlanError
from .executor import EntryResult, Executor, PathError
from .logutil import EventLogger

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_CONFIG = 2
EXIT_NOTHING = 3


def _selected(plan: ApplyPlan, only: str | None) -> None:
    """--only 进一步筛选（序号沿用 preview 的编号，不是列表下标）。"""
    if not only:
        return
    wanted = {int(x) for x in only.split(",") if x.strip().isdigit()}
    if not wanted:
        raise PlanError("--only 里没有合法序号")
    plan.entries = [
        p for p in plan.entries if plan.seq_by_root.get(p.root, -1) in wanted
    ]
    known = {p.root for p in plan.entries}
    plan.rows = [row for row in plan.rows if row.get("源目录") in known]
    plan.seq_by_root = {k: v for k, v in plan.seq_by_root.items() if k in known}


def _disk_precheck(ex: Executor, plan: ApplyPlan, cfg: dict[str, Any]) -> None:
    """跨设备/复制模式才需要额外空间；同设备 move 不占额外空间。"""
    need: dict[str, int] = {}
    for item in plan.entries:
        for source, target, size, _kind in ex._planned_files(item):
            if not target:
                continue
            try:
                dst = ex.target_path(target)
                src = ex.source_path(source)
            except PathError:
                continue
            if size <= 0:
                with contextlib.suppress(OSError):
                    size = src.stat().st_size  # 电影计划不带 size，以磁盘上的实际大小为准
            if size <= 0:
                continue
            parent = dst.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            same_device = False
            with contextlib.suppress(OSError):
                same_device = src.stat().st_dev == parent.stat().st_dev
            if cfg.get("behavior", {}).get("mode", "move") != "copy" and same_device:
                continue
            key = target.split("/", 1)[0]
            need[key] = need.get(key, 0) + size
    for key, size in need.items():
        lib = ex.libs.get(key)
        if not lib:
            continue
        try:
            free = shutil.disk_usage(lib).free
        except OSError:
            continue
        if size > free:
            raise PlanError(
                f"{key} 库所在磁盘空间不足：需要 {media.human_size(size)}，"
                f"剩余 {media.human_size(free)}，还差 {media.human_size(size - free)}"
            )


def _write_manifests(
    ex: Executor,
    plan_by_root: dict[str, Any],
    results: list[EntryResult],
    cfg: dict[str, Any],
    logger: EventLogger,
    now: datetime,
) -> None:
    if not cfg.get("manifest", {}).get("enabled", True):
        return
    mname = str(cfg.get("manifest", {}).get("name", "jf-ingest"))
    mfmt = str(cfg.get("manifest", {}).get("format", "md")).lower()
    filename = f"{mname}.{mfmt if mfmt in {'md', 'txt'} else 'md'}"
    for res in results:
        if res.status not in {"success", "partial"}:
            continue
        entry = plan_by_root.get(res.root)
        if entry is None:
            continue
        try:
            item_dir = ex.target_path(ex.item_dir_logical(entry))
            if not item_dir.is_dir():
                continue
            rows = [
                manifest.MappingRow(
                    manifest.strip_root(f.source, res.root), f.rel_current,
                    f.status, f.reason,
                )
                for f in res.files if f.target
            ]
            mapped = {f.source for f in res.files if f.target}
            for source in entry.source_files:
                if source not in mapped:
                    rows.append(manifest.MappingRow(
                        manifest.strip_root(source, res.root), "", "SKIPPED",
                        "未安排目标或未搬运",
                    ))
            data = manifest.ManifestData(
                title=res.title or entry.title,
                kind=res.kind,
                year=res.year,
                source_root=res.root,
                first_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                last_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                result=applyreport.STATUS_LABEL.get(res.status, res.status),
                original=manifest.tree_lines(res.root, entry.source_files),
                current=manifest.current_tree(item_dir, filename),
                rows=rows,
            )
            name, code = manifest.write(item_dir, data, cfg, logger, now)
            res.manifest_file = name
            res.manifest_code = code
        except (PathError, OSError) as exc:
            logger.error("MANIFEST_FAIL", f"写入入库记录失败：{exc}", root=res.root)
            res.notes.append(f"入库记录写入失败：{exc}")


def _print_summary(results: list[EntryResult], refresh: Any, paths: dict[str, Path],
                   exit_code: int, logger: EventLogger) -> None:
    from collections import Counter

    by_entry = Counter(r.status for r in results)
    by_file = Counter(f.status for r in results for f in r.files)
    print()
    print("=" * 66)
    print("  jf-ingest 执行完成" + ("（有失败/冲突）" if exit_code else "（全部成功）"))
    print("=" * 66)
    print(
        f"  条目：成功 {by_entry['success']} / 部分失败 {by_entry['partial']}"
        f" / 失败 {by_entry['failed']} / 跳过 {by_entry['skipped']}"
    )
    print(
        f"  文件：落盘 {by_file['OK']} / 新增版本 {by_file['VERSION_ADDED']}"
        f" / 已存在跳过 {by_file['SKIP_SAME']}"
        f" / 冲突 {by_file['CONFLICT']} / 失败 {by_file['ERROR']}"
    )
    print(f"  Jellyfin 刷新：{refresh.label}")
    print(f"  日志：{logger.log_path}")
    print("  报告：")
    for key in ("md", "json"):
        print(f"    {paths[key]}")
    print()


def run_apply(cfg: dict[str, Any], args: Any) -> int:
    started = datetime.now()
    run_id = started.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    report_dir = Path(cfg["paths"]["reports"]).expanduser()
    try:
        report.guard_report_dir(cfg, report_dir)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG

    logger = EventLogger(report_dir, verbose=bool(getattr(args, "verbose", False)), run_id=run_id)
    logger.event("RUN_START", "apply 开始", mode=cfg.get("behavior", {}).get("mode", "move"))
    try:
        for check in (jellyfin.validate(cfg),):
            if check:
                raise PlanError(check)

        plan = applyplan.load(args.plan, cfg, logger, listing=getattr(args, "from_listing", None))
        _selected(plan, getattr(args, "only", None))
        if not plan.entries:
            print("确认表里没有可执行的条目。", file=sys.stderr)
            return EXIT_NOTHING

        ex = Executor(cfg, logger)
        _disk_precheck(ex, plan, cfg)
        if jellyfin.api_key(cfg) and cfg.get("jellyfin", {}).get("refresh", True):
            jellyfin.probe(cfg, logger)

        results: list[EntryResult] = []
        plan_by_root = {p.root: p for p in plan.entries}
        for i, entry in enumerate(plan.entries, 1):
            seq = plan.seq_by_root.get(entry.root, i)
            results.append(ex.execute_entry(entry, seq))

        now = datetime.now()
        _write_manifests(ex, plan_by_root, results, cfg, logger, now)

        paths_to_refresh = [d for r in results for d in r.target_dirs]
        refresh = jellyfin.refresh(cfg, paths_to_refresh, logger)
        reporter = applyreport.ApplyReporter(cfg, run_id, started, plan, results, refresh, now)
        paths = reporter.write_all(report_dir)

        bad = [r for r in results if r.status in {"partial", "failed"}]
        conflicts = [f for r in results for f in r.files if f.status == "CONFLICT"]
        exit_code = EXIT_PARTIAL if bad or conflicts else EXIT_OK
        logger.event(
            "RUN_END", f"apply 结束，退出码 {exit_code}",
            entries=len(results), bad=len(bad), conflicts=len(conflicts),
        )
        _print_summary(results, refresh, paths, exit_code, logger)
        return exit_code

    except (PlanError, PathError, config.ConfigError) as exc:
        logger.error("ERROR", f"apply 无法继续：{exc}")
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_CONFIG
    except KeyboardInterrupt:
        logger.warning("INTERRUPTED", "收到 Ctrl+C，已完成条目保留，未完成的请重跑预览确认")
        print("\n已中断；已完成条目保留，未完成部分请重跑预览确认。", file=sys.stderr)
        return EXIT_PARTIAL
    finally:
        logger.close()


__all__ = ["run_apply"]
