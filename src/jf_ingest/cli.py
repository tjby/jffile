"""命令行入口。

阶段一只有 `preview`：扫描 + 识别 + 写报告，**不移动任何文件**。
`apply` 会在阶段二实现。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, analyze, config, media, report, scan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jf-ingest",
        description="把上传目录整理成 Jellyfin 结构（当前：只读预览）",
    )
    parser.add_argument("--version", action="version", version=f"jf-ingest {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("preview", help="扫描并生成预览报告（不改动任何文件）")
    p.add_argument("--config", help="配置文件（JSON）")
    p.add_argument("--source", help="覆盖源目录")
    p.add_argument("--report-dir", help="报告输出目录（默认 ./logs）")
    p.add_argument("--movies", help="覆盖电影库路径（仅用于报告里的路径展示）")
    p.add_argument("--tvshows", help="覆盖剧集库路径")
    p.add_argument(
        "--from-listing",
        help="从 scan-upload.sh 生成的 TSV 读取清单（离线分析，不访问磁盘）",
    )
    p.add_argument("--only", help="只分析指定序号，逗号分隔（如 3,17,42）")
    p.add_argument("--limit", type=int, help="只分析前 N 个条目")
    p.add_argument(
        "--map-subtitle-language",
        action="store_true",
        help="字幕标签映射成 zh-CN/zh-TW（默认关闭，只记录）",
    )
    p.add_argument(
        "--max-episodes",
        type=int,
        default=None,
        help="Markdown 里每个条目最多展示多少条集映射（完整数据在 JSON）",
    )
    p.add_argument("--verbose", action="store_true")

    p = sub.add_parser("apply", help="（阶段二，尚未实现）")
    p.add_argument("--plan", help="确认表 TSV")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "apply":
        print(
            "apply 尚未实现（阶段二）。\n"
            "当前只做只读预览：python3 -m jf_ingest preview --from-listing <清单.tsv>",
            file=sys.stderr,
        )
        return 2

    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    if args.source:
        cfg["paths"]["source"] = args.source
    if args.movies:
        cfg["paths"]["movies"] = args.movies
    if args.tvshows:
        cfg["paths"]["tvshows"] = args.tvshows
    if args.report_dir:
        cfg["paths"]["reports"] = args.report_dir
    if args.map_subtitle_language:
        cfg["subtitle"]["map_language"] = True
    if args.max_episodes is not None:
        cfg["behavior"]["max_episodes_in_md"] = args.max_episodes

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
    reporter = report.Reporter(cfg, source, stats, plans)
    paths = reporter.write_all(cfg["paths"]["reports"])

    _print_summary(plans, stats, paths)
    return 0


def _print_summary(plans: list[analyze.EntryPlan], stats: dict, paths: dict[str, Path]) -> None:
    import collections

    kinds = collections.Counter(p.kind for p in plans)
    confs = collections.Counter(p.confidence for p in plans)
    issues = collections.Counter(i for p in plans for i in p.issues)

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
