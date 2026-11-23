"""FR-17 入库记录：写在条目文件夹根目录（剧集是剧名根目录，不是 Season 0X）。

固定四段：摘要 / 原始结构 / 当前结构 / 改名对照。
- 路径一律 POSIX 相对路径，库整体搬家后仍然有效；
- 已有本程序生成的清单 → 增量合并（保留「首次建档」和旧行，按当前路径去重）；
- 已有同名但不是本程序写的 → 不覆盖，另存 `jf-ingest.<时间>.md`。
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import media
from .logutil import EventLogger

MD_MARKER = "<!-- jf-ingest:v1 -->"
TXT_MARKER = "# jf-ingest record v1"

STATUS_MOVED = {"OK", "SKIP_SAME", "VERSION_ADDED"}


@dataclass
class MappingRow:
    source: str            # 相对源根
    current: str           # 相对条目文件夹；空 = 没搬
    status: str = "OK"     # OK / SKIP_SAME / VERSION_ADDED / CONFLICT / SKIPPED / ERROR
    reason: str = ""

    def key(self) -> str:
        return self.current or f"SKIP::{self.source}"


@dataclass
class ManifestData:
    title: str
    kind: str
    year: int | None
    source_root: str
    first_at: str
    last_at: str
    result: str                       # 成功 / 部分失败 / 跳过
    original: list[str] = field(default_factory=list)   # 已带树形符号的行
    current: list[str] = field(default_factory=list)
    rows: list[MappingRow] = field(default_factory=list)


# ---------------------------------------------------------------- 树


def _strip_root(path: str, root_name: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
    if parts and parts[0] == root_name:
        parts = parts[1:]
    return "/".join(parts)


def strip_root(path: str, root: str) -> str:
    """`片名/子目录/a.mkv` → `子目录/a.mkv`（相对条目根目录）。"""
    prefix = root.rstrip("/") + "/"
    return path[len(prefix):] if path.startswith(prefix) else path


def tree_lines(root_name: str, paths: Iterable[str]) -> list[str]:
    """把相对路径列表渲染成 `├── / └──` 树。"""
    root: dict[str, Any] = {}
    for raw in paths:
        parts = [p for p in _strip_root(raw, root_name).split("/") if p]
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if parts:
            node[parts[-1]] = None

    out = [f"{root_name.rstrip('/')}/"]

    def walk(node: dict[str, Any], prefix: str) -> None:
        keys = sorted(node, key=lambda k: (node[k] is not None, k.lower()))
        for i, key in enumerate(keys):
            last = i == len(keys) - 1
            child = node[key]
            branch = "└── " if last else "├── "
            out.append(prefix + branch + key + ("/" if isinstance(child, dict) else ""))
            if isinstance(child, dict):
                walk(child, prefix + ("    " if last else "│   "))

    walk(root, "")
    return out


def current_tree(item_dir: Path, manifest_name: str) -> list[str]:
    """扫描目标文件夹的真实内容（含清单自身）。"""
    paths: list[str] = []
    for base, dirs, files in os.walk(item_dir):
        dirs.sort()
        for name in sorted(files):
            rel = Path(base).relative_to(item_dir).as_posix()
            rel = f"{rel}/{name}" if rel != "." else name
            if rel == manifest_name:
                continue
            paths.append(rel)
    paths.append(manifest_name)
    return tree_lines(item_dir.name, paths)


# ---------------------------------------------------------------- 渲染


def _code(value: str) -> str:
    """Markdown 表格里把文件名放进反引号，避免 [ ] | * 破坏结构。"""
    return "`" + value.replace("`", "'") + "`"


def render(data: ManifestData, fmt: str = "md") -> str:
    if fmt == "txt":
        return _render_txt(data)
    return _render_md(data)


def _render_md(data: ManifestData) -> str:
    lines = [
        f"# {data.title}",
        "",
        MD_MARKER,
        "",
        f"- 类型：{media.KIND_LABEL_ZH.get(data.kind, data.kind)}",
        f"- 年份：{data.year or '（无）'}",
        f"- 源目录：{_code(data.source_root)}",
        f"- 首次建档：{data.first_at} ｜ 最后更新：{data.last_at}",
        f"- 结果：{data.result}（{len(data.rows)} 条记录）",
        "",
        "## 原始结构",
        "",
        "```text",
        *data.original,
        "```",
        "",
        "## 当前结构",
        "",
        "```text",
        *data.current,
        "```",
        "",
        "## 改名对照",
        "",
        "| 原始 | 当前 | 状态 |",
        "| --- | --- | --- |",
    ]
    for row in data.rows:
        cur = _code(row.current) if row.current else "—"
        state = row.status if not row.reason else f"{row.status}：{row.reason}"
        lines.append(f"| {_code(row.source)} | {cur} | {state} |")
    return "\n".join(lines) + "\n"


def _render_txt(data: ManifestData) -> str:
    lines = [
        f"# {data.title}",
        TXT_MARKER,
        f"类型: {media.KIND_LABEL_ZH.get(data.kind, data.kind)}",
        f"年份: {data.year or '(无)'}",
        f"源目录: {data.source_root}",
        f"首次建档: {data.first_at} | 最后更新: {data.last_at}",
        f"结果: {data.result} ({len(data.rows)} 条记录)",
        "",
        "## 原始结构",
        "",
        *data.original,
        "",
        "## 当前结构",
        "",
        *data.current,
        "",
        "## 改名对照",
        "",
    ]
    for row in data.rows:
        cur = row.current or "SKIP"
        if row.reason:
            cur = f"{cur}（{row.reason}）"
        lines.append(f"{row.source}  --  {cur}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 解析 / 合并

_FIRST_RE = re.compile(r"首次建档[：:]\s*(.+?)\s*[｜|]")


def is_generated(text: str, fmt: str = "md") -> bool:
    return (MD_MARKER if fmt == "md" else TXT_MARKER) in text


def parse_existing(text: str, fmt: str = "md") -> tuple[str, list[MappingRow]]:
    """读旧清单，返回 (首次建档时间, 旧行)。只认本程序生成的标记。"""
    if not is_generated(text, fmt):
        return "", []
    m = _FIRST_RE.search(text)
    first_at = m.group(1) if m else ""
    rows: list[MappingRow] = []
    if fmt == "txt":
        for line in text.splitlines():
            if "  --  " not in line:
                continue
            source, current = line.split("  --  ", 1)
            status, _, reason = current.partition("（")
            reason = reason.rstrip("）")
            rows.append(MappingRow(source.strip(), "", status.strip(), reason))
        return first_at, rows

    in_table = False
    for line in text.splitlines():
        if line.strip() == "## 改名对照":
            in_table = True
            continue
        if not in_table or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in {"原始", "---"} or set(cells[0]) <= {"-"}:
            continue
        source = cells[0]
        current = "" if cells[1] == "—" else cells[1]
        status, sep, reason = cells[2].partition("：") if len(cells) > 2 else ("OK", "", "")
        rows.append(MappingRow(source, current, status, reason if sep else ""))
    return first_at, rows


def merge_rows(old: list[MappingRow], new: list[MappingRow]) -> list[MappingRow]:
    """按「当前路径」去重；旧行在前、新行在后，新值覆盖同 key 的旧值。"""
    index = {row.key(): i for i, row in enumerate(old)}
    out = list(old)
    for row in new:
        key = row.key()
        if key in index:
            out[index[key]] = row
        else:
            index[key] = len(out)
            out.append(row)
    return out


# ---------------------------------------------------------------- 写盘


def write(
    item_dir: Path,
    data: ManifestData,
    cfg: dict[str, Any],
    logger: EventLogger,
    now: datetime,
) -> tuple[str, str]:
    """返回 (实际写入的文件名, 事件码)。不抛异常（失败由调用方记为条目失败）。"""
    mcfg = cfg.get("manifest", {})
    if not mcfg.get("enabled", True):
        return "", "MANIFEST_DISABLED"
    fmt = str(mcfg.get("format", "md")).lower()
    if fmt not in {"md", "txt"}:
        fmt = "md"
    name = str(mcfg.get("name", "jf-ingest"))
    filename = f"{name}.{fmt}"
    path = item_dir / filename
    conflict = False

    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        if not is_generated(text, fmt):
            stamp = now.strftime("%Y%m%d-%H%M%S")
            filename = f"{name}.{stamp}.{fmt}"
            path = item_dir / filename
            conflict = True
            logger.warning(
                "MANIFEST_CONFLICT", "同名清单不是本程序写的，另存新文件",
                file=filename,
            )
        elif mcfg.get("merge", True):
            first_at, old_rows = parse_existing(text, fmt)
            if first_at:
                data.first_at = first_at
            data.rows = merge_rows(old_rows, data.rows)

    if not mcfg.get("include_skipped", True):
        data.rows = [r for r in data.rows if r.status in STATUS_MOVED]

    body = render(data, fmt)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o644)
    logger.event("MANIFEST_OK", "已写入入库记录", file=str(path), rows=len(data.rows))
    return filename, "MANIFEST_CONFLICT" if conflict else "OK"


__all__ = [
    "MappingRow", "ManifestData", "is_generated", "merge_rows", "parse_existing",
    "render", "strip_root", "tree_lines", "current_tree", "write",
]
