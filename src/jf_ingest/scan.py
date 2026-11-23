"""扫描：从磁盘或清单（TSV）得到文件列表，并按顶层条目分组。"""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import media


@dataclass
class ScanResult:
    files: list[media.FileInfo] = field(default_factory=list)
    skipped_hidden: int = 0
    skipped_junk: int = 0
    skipped_unstable: int = 0
    skipped_empty: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


def _is_hidden(rel: str) -> bool:
    return any(part.startswith(".") for part in rel.split("/"))


def scan_filesystem(
    root: str | Path,
    stable_seconds: float = 0,
    follow_symlinks: bool = False,
    now: float | None = None,
) -> ScanResult:
    """递归扫描源目录。只读，不跟随符号链接，不进入隐藏目录。"""
    base = Path(root)
    res = ScanResult()
    if not base.exists():
        res.errors.append(f"源目录不存在: {base}")
        return res

    now = time.time() if now is None else now
    stack = [base]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            res.errors.append(f"无法读取目录 {current}: {exc}")
            continue

        for entry in entries:
            rel = entry.path[len(str(base)) + 1:].replace(os.sep, "/")
            if _is_hidden(rel):
                res.skipped_hidden += 1
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
                is_link = entry.is_symlink()
            except OSError as exc:
                res.errors.append(f"无法读取 {entry.path}: {exc}")
                continue

            if is_dir:
                if is_link and not follow_symlinks:
                    res.skipped_hidden += 1
                    continue
                stack.append(Path(entry.path))
                continue

            if is_link and not follow_symlinks:
                res.skipped_hidden += 1
                continue

            name = entry.name
            ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if ext in media.JUNK_EXT:
                res.skipped_junk += 1
                continue

            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                res.errors.append(f"无法读取 {entry.path}: {exc}")
                continue

            if stat.st_size == 0:
                res.skipped_empty += 1
                continue
            if stable_seconds and now - stat.st_mtime < stable_seconds:
                res.skipped_unstable += 1
                continue

            res.files.append(media.FileInfo(rel, stat.st_size, stat.st_mtime))

    res.files.sort(key=lambda f: f.path)
    return res


def load_listing(path: str | Path) -> ScanResult:
    """从 `tools/scan-upload.sh` 生成的 TSV 读取清单（本地离线分析用）。

    格式：类型 \\t 字节数 \\t mtime \\t 相对路径
    """
    res = ScanResult()
    p = Path(path)
    if not p.exists():
        res.errors.append(f"清单文件不存在: {p}")
        return res

    for line in p.read_text(encoding="utf-8").splitlines():
        if not line or line[0] not in "fdl":
            continue
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        kind, size, mtime, rel = parts
        if kind != "f":
            continue
        rel = rel.replace("\\", "/")
        if _is_hidden(rel):
            res.skipped_hidden += 1
            continue
        ext = ("." + rel.rsplit(".", 1)[-1].lower()) if "." in rel.rsplit("/", 1)[-1] else ""
        if ext in media.JUNK_EXT:
            res.skipped_junk += 1
            continue
        try:
            size_i = int(size)
        except ValueError:
            size_i = 0
        if size_i == 0:
            res.skipped_empty += 1
            continue
        ts = 0.0
        with contextlib.suppress(ValueError):
            ts = datetime.strptime(mtime.strip(), "%Y-%m-%d %H:%M").timestamp()
        res.files.append(media.FileInfo(rel, size_i, ts))

    res.files.sort(key=lambda f: f.path)
    return res


def build_entries(files: list[media.FileInfo]) -> list[media.Entry]:
    """按顶层目录分组；源目录根下的散文件各自成为一个条目。"""
    entries: dict[str, media.Entry] = {}
    for f in files:
        root = f.path.split("/", 1)[0] if "/" in f.path else f.path
        entries.setdefault(root, media.Entry(root=root)).files.append(f)
    return sorted(entries.values(), key=lambda e: e.root)
