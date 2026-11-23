"""命令行入口：preview（只读预览）/ apply（按确认表执行）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, analyze, apply, config, media, report, scan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jf-ingest",
        description="把上传目录整理成 Jellyfin 结构",
    )
    parser.add_argument("--version", action="version", version=f"jf-ingest {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("preview", help="扫描并生成预览报告（不改动任何文件）")
    _add_common(p, listing=True)
    p.add_argument(
        "--map-subtitle-language", action="store_true",
        help="字幕标签映射成 zh-CN/zh-TW（默认关闭，只记录）",
    )
    p.add_argument(
        "--max-episodes", type=int, default=None,
        help="Markdown 里每个条目最多展示多少条集映射（完整数据在 JSON）",
    )
    p.add_argument("--limit", type=int, help="只分析前 N 个条目")

    p = sub.add_parser("apply", help="按确认表执行（先跑 preview 再看报告）")
    _add_common(p, listing=True)
    p.add_argument("--plan", required=True, help="预览生成的 *-plan.tsv 确认表")
    p.add_argument("--mode", choices=("move", "copy", "hardlink"), help="落盘策略（默认 move）")
    p.add_argument("--on-conflict", choices=("skip", "suffix"),
                   help="目标冲突时怎么做（默认 skip）")
    p.add_argument("--prune-empty-dirs", action="store_true", help="处理完删除已空的源目录")
    p.add_argument("--chown", dest="chown_owner", metavar="USER:GROUP",
                   help="设置目标属主（需 root；非 root 只告警）")
    p.add_argument("--no-chown", dest="chown", action="store_false", default=None,
                   help="不改属主（默认）")
    refresh_group = p.add_mutually_exclusive_group()
    refresh_group.add_argument("--refresh", dest="refresh", action="store_true", default=None,
                               help="执行后调用 Jellyfin 刷新（默认按配置）")
    refresh_group.add_argument("--no-refresh", dest="refresh", action="store_false",
                               help="不调用 Jellyfin 刷新")
    p.add_argument("--no-manifest", action="store_true", help="不写 jf-ingest.md 入库记录")
    p.add_argument("--manifest-name", help="入库记录文件名（不含扩展名，默认 jf-ingest）")
    p.add_argument("--manifest-format", choices=("md", "txt"), help="入库记录格式（默认 md）")
    return parser


def _add_common(p: argparse.ArgumentParser, listing: bool = False) -> None:
    p.add_argument("--config", help="配置文件（JSON）")
    p.add_argument("--source", help="覆盖源目录")
    p.add_argument("--report-dir", help="报告输出目录（默认 ./logs）")
    p.add_argument("--movies", help="覆盖电影库路径")
    p.add_argument("--tvshows", help="覆盖剧集库路径")
    if listing:
        p.add_argument(
            "--from-listing",
            help="从 scan-upload.sh 生成的 TSV 读取清单（离线分析，不访问磁盘）",
        )
    p.add_argument("--only", help="只处理指定序号，逗号分隔（如 3,17,42）")
    p.add_argument("--verbose", action="store_true")


def _overrides(cfg: dict, args: argparse.Namespace) -> None:
    """命令行参数覆盖配置；apply 独有参数用 getattr 兼容 preview。"""
    if args.source:
        cfg["paths"]["source"] = args.source
    if args.movies:
        cfg["paths"]["movies"] = args.movies
    if args.tvshows:
        cfg["paths"]["tvshows"] = args.tvshows
    if args.report_dir:
        cfg["paths"]["reports"] = args.report_dir
    if getattr(args, "map_subtitle_language", False):
        cfg["subtitle"]["map_language"] = True
    if getattr(args, "max_episodes", None) is not None:
        cfg["behavior"]["max_episodes_in_md"] = args.max_episodes

    if getattr(args, "mode", None):
        cfg["behavior"]["mode"] = args.mode
    if getattr(args, "on_conflict", None):
        cfg["behavior"]["on_conflict"] = args.on_conflict
    if getattr(args, "prune_empty_dirs", False):
        cfg["behavior"]["prune_empty_dirs"] = True
    if getattr(args, "chown_owner", None):
        cfg["permissions"]["enabled"] = True
        cfg["permissions"]["owner"] = args.chown_owner
    if getattr(args, "chown", None) is False:
        cfg["permissions"]["enabled"] = False
    if getattr(args, "refresh", None) is not None:
        cfg["jellyfin"]["refresh"] = args.refresh
    if getattr(args, "no_manifest", False):
        cfg["manifest"]["enabled"] = False
    if getattr(args, "manifest_name", None):
        cfg["manifest"]["name"] = args.manifest_name
    if getattr(args, "manifest_format", None):
        cfg["manifest"]["format"] = args.manifest_format


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 2

    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    _overrides(cfg, args)

    if args.command == "apply":
        return apply.run_apply(cfg, args)

    source = cfg["paths"]["source"]
    if args.from_listing:
        result = scan.load_listing(args.from_listing)
        if args.verbose:
            print(f"从清单读取: {args.from_listing}")
    else:
        result = scan.scan_filesystem(
            source,
            stable_seconds=float(cfg["behavior"].get("stable_seconds", 0)),
            follow_symlinks=bool(cfg["behavior"].get("follow_symlinks", False)),
        )

    for message in result.errors:
        print(f"[warn] {message}", file=sys.stderr)

    entries = scan.build_entries(result.files)
    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip().isdigit()}
        entries = [e for i, e in enumerate(entries, 1) if i in wanted]
    if args.limit:
        entries = entries[: args.limit]

    if not entries:
        print("没有扫描到任何文件。检查源目录或清单路径。", file=sys.stderr)
        return 3

    plans = [analyze.build(entry, cfg) for entry in entries]
    stats = {
        "files": len(result.files),
        "size": result.total_size,
        "hidden": result.skipped_hidden,
        "junk": result.skipped_junk,
        "empty": result.skipped_empty,
        "unstable": result.skipped_unstable,
    }
    try:
        report.guard_report_dir(cfg, Path(cfg["paths"]["reports"]))
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    reporter = report.Reporter(cfg, source, stats, plans)
    paths = reporter.write_all(cfg["paths"]["reports"])
    _print_summary(plans, stats, paths)
    return 0


def _print_summary(plans: list[analyze.EntryPlan], stats: dict, paths: dict[str, Path]) -> None:
    from collections import Counter

    kinds = Counter(p.kind for p in plans)
    confs = Counter(p.confidence for p in plans)
    issues = Counter(i for p in plans for i in p.issues)

    print()
    print("=" * 66)
    print("  jf-ingest 预览完成（未改动任何文件）")
    print("=" * 66)
    print(
        f"  条目 {len(plans)}：电影 {kinds['movie']} / 剧集 {kinds['tv']}"
        f" / 未识别 {kinds['unknown']}"
    )
    print(f"  置信度：高 {confs['high']} / 中 {confs['medium']} / 低 {confs['low']}")
    print(f"  扫描文件 {stats['files']} 个（{media.human_size(stats['size'])}）")
    if issues:
        top = "、".join(
            f"{media.ISSUE_LABEL.get(k, k)}×{v}" for k, v in issues.most_common(6)
        )
        print(f"  待确认：{top}")
    print()
    print("  报告：")
    for key in ("md", "tsv", "json"):
        print(f"    {paths[key]}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
