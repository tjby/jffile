"""apply 执行引擎：落盘、冲突判定、幂等、权限、空目录清理。

安全约束（NFR-1）：
- 源路径必须在 paths.source 之内；
- 目标路径必须以 movies/ 或 tvshows/ 开头，且解析后仍在对应库目录内；
- 绝不覆盖内容不同的已存在文件（默认 on_conflict=skip）。
"""

from __future__ import annotations

import contextlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from . import analyze, media, naming, report
from .logutil import EventLogger

OK_STATES = {"OK", "SKIP_SAME", "VERSION_ADDED"}
BAD_STATES = {"CONFLICT", "ERROR", "SKIPPED"}


@dataclass
class FileResult:
    source: str
    target: str = ""          # 计划中的逻辑目标（movies/… 或 tvshows/…）
    actual: str = ""          # 冲突/版本处理后真正落地的逻辑目标
    status: str = "SKIPPED"   # OK / SKIP_SAME / VERSION_ADDED / CONFLICT / SKIPPED / ERROR
    reason: str = ""
    action: str = "-"         # move / copy / link
    size: int = 0

    @property
    def success(self) -> bool:
        return self.status in OK_STATES

    @property
    def rel_current(self) -> str:
        """相对条目文件夹的路径（FR-17 改名对照用）。"""
        parts = [p for p in PurePosixPath(self.actual).parts if p]
        return "/".join(parts[2:]) if len(parts) > 2 else (parts[-1] if parts else "")


@dataclass
class EntryResult:
    seq: int
    root: str
    kind: str
    title: str
    year: int | None
    season: int | None
    status: str = "skipped"   # success / partial / failed / skipped
    files: list[FileResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    manifest_file: str = ""
    manifest_code: str = ""
    target_dirs: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len([f for f in self.files if f.status in {"OK", "VERSION_ADDED"}])


class PathError(ValueError):
    """路径越界或格式错误。"""


def _parts(rel: str) -> list[str]:
    parts = PurePosixPath(str(rel).replace("\\", "/")).parts
    if ".." in parts:
        raise PathError(f"路径包含 ..：{rel}")
    return [p for p in parts if p not in ("", ".", "/")]


class Executor:
    def __init__(self, cfg: dict[str, Any], logger: EventLogger) -> None:
        self.cfg = cfg
        self.logger = logger
        paths = cfg["paths"]
        self.source_root = Path(paths["source"]).expanduser().resolve()
        self.libs = {
            "movies": Path(paths["movies"]).expanduser().resolve(),
            "tvshows": Path(paths["tvshows"]).expanduser().resolve(),
        }
        behavior = cfg.get("behavior", {})
        self.mode = str(behavior.get("mode", "move")).lower()
        if self.mode not in {"move", "copy", "hardlink"}:
            raise PathError(f"behavior.mode 只能是 move/copy/hardlink，当前 {self.mode!r}")
        self.on_conflict = str(behavior.get("on_conflict", "skip")).lower()
        if self.on_conflict not in {"skip", "suffix"}:
            raise PathError(f"behavior.on_conflict 只能是 skip/suffix，当前 {self.on_conflict!r}")
        self.prune = bool(behavior.get("prune_empty_dirs", False))

        perm = cfg.get("permissions", {})
        self.do_chown = bool(perm.get("enabled", False))
        self.owner = str(perm.get("owner", "jellyfin:jellyfin"))
        self.dir_mode = _mode(perm.get("dir_mode", "0755"), 0o755)
        self.file_mode = _mode(perm.get("file_mode", "0644"), 0o644)
        self._chown_reported = False
        self._used: set[str] = set()

    # ------------------------------------------------------------ 路径

    def source_path(self, rel: str) -> Path:
        path = self.source_root.joinpath(*_parts(rel)).resolve()
        if not media.is_under(path, self.source_root):
            raise PathError(f"源文件越界：{rel}")
        return path

    def target_path(self, logical: str) -> Path:
        parts = _parts(logical)
        if len(parts) < 2 or parts[0] not in self.libs:
            raise PathError(f"目标必须位于 movies/ 或 tvshows/ 下：{logical}")
        lib = self.libs[parts[0]]
        path = lib.joinpath(*parts[1:]).resolve()
        if not media.is_under(path, lib):
            raise PathError(f"目标越界：{logical}")
        return path

    def item_dir_logical(self, plan: analyze.EntryPlan) -> str:
        if plan.movie_target:
            return plan.movie_target.rsplit("/", 1)[0]
        targets = [e.target for e in plan.episodes if e.target]
        if targets:
            parts = targets[0].split("/")
            return "/".join(parts[:-2]) if len(parts) >= 3 else targets[0]
        return naming.join("tvshows", naming.show_folder(plan.title, plan.year))

    # ------------------------------------------------------------ 单文件

    @staticmethod
    def _same_content(src: Path, dst: Path) -> bool:
        """逐字节比较；mtime 在同一秒内可能相同，绝不能只看大小+mtime。"""
        try:
            if src.stat().st_size != dst.stat().st_size:
                return False
            with src.open("rb") as left, dst.open("rb") as right:
                while True:
                    a, b = left.read(1024 * 1024), right.read(1024 * 1024)
                    if a != b:
                        return False
                    if not a:
                        return True
        except OSError:
            return False

    @staticmethod
    def _same_device(a: Path, b: Path) -> bool:
        try:
            return a.stat().st_dev == b.stat().st_dev
        except OSError:
            return False

    def _ensure_dir(self, path: Path) -> None:
        missing: list[Path] = []
        cur = path
        while not cur.exists() and cur != cur.parent:
            missing.append(cur)
            cur = cur.parent
        path.mkdir(parents=True, exist_ok=True)
        for item in reversed(missing):
            with contextlib.suppress(OSError):
                os.chmod(item, self.dir_mode)

    def _chown(self, path: Path) -> None:
        if not self.do_chown:
            return
        try:
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                raise PermissionError("非 root")
            user, _, group = self.owner.partition(":")
            group = group or user
            import grp
            import pwd
            uid = pwd.getpwnam(user).pw_uid
            gid = grp.getgrnam(group).gr_gid
            os.chown(path, uid, gid)
            self.logger.event("CHOWN_OK", "属主已设置", path=str(path), owner=self.owner)
        except (ImportError, KeyError, OSError, PermissionError) as exc:
            if not self._chown_reported:
                self.logger.warning(
                    "CHOWN_SKIP", f"跳过属主设置（{type(exc).__name__}: {exc}），迁移照常完成",
                    owner=self.owner,
                )
                self._chown_reported = True

    def _pick_target(self, dst: Path, src: Path) -> tuple[Path, str, str]:
        """返回 (最终目标, 状态, 说明)。"""
        if not dst.exists() and not dst.is_symlink():
            return dst, "OK", ""
        if self._same_content(src, dst):
            return dst, "SKIP_SAME", "目标已存在且内容一致"

        # 默认 skip：目标不同就报冲突，绝不擅自另放一个文件。
        # 只有 --on-conflict suffix 才尝试版本名 / 数字后缀，实现多版本共存。
        stem, ext = _stem_ext(dst.name)
        label = naming.version_from_name(src.name)
        attempts: list[str] = []
        if self.on_conflict == "suffix":
            if label:
                attempts.append(naming.with_version(stem, ext, label))
                for n in range(2, 10):
                    attempts.append(naming.with_version(stem, ext, f"{label} ({n})"))
            attempts.append(f"{stem} (2){ext}")
            for n in range(3, 10):
                attempts.append(f"{stem} ({n}){ext}")

        for candidate in attempts:
            alt = dst.with_name(candidate)
            if not alt.exists() and not alt.is_symlink():
                state = "VERSION_ADDED" if label and candidate.startswith(stem + " - ") else "OK"
                reason = "新增版本" if state == "VERSION_ADDED" else "冲突时改用新文件名"
                return alt, state, reason

        return dst, "CONFLICT", "目标已存在且内容不同，未覆盖"

    def place(self, source: str, logical: str, expected_size: int = 0) -> FileResult:
        result = FileResult(source=source, target=logical, size=expected_size)
        if not logical:
            result.reason = "预览未给出目标路径"
            return result
        try:
            src = self.source_path(source)
            dst = self.target_path(logical)
        except PathError as exc:
            result.status = "ERROR"
            result.reason = str(exc)
            self.logger.error("ERROR", str(exc), src=source, dst=logical)
            return result

        if src.is_symlink():
            result.reason = "源文件是符号链接，跳过"
            self.logger.warning("SKIP_SYMLINK", result.reason, src=source)
            return result
        if not src.is_file():
            result.reason = "源文件不存在（可能已被移走）"
            self.logger.warning("SKIP_MISSING", result.reason, src=source)
            return result

        actual, state, note = self._pick_target(dst, src)
        result.actual = _logical_from_abs(actual, self.libs)
        result.status = state
        result.reason = note

        if state == "CONFLICT":
            self.logger.warning("CONFLICT", note, src=source, dst=result.actual)
            return result
        if state == "SKIP_SAME":
            self.logger.event("SKIP_SAME", note, src=source, dst=result.actual)
            return result

        if result.actual in self._used:
            result.status = "CONFLICT"
            result.reason = "同一批内两个源映射到同一目标"
            self.logger.warning("CONFLICT", result.reason, src=source, dst=result.actual)
            return result

        try:
            self._ensure_dir(actual.parent)
            if self.mode == "copy":
                shutil.copy2(src, actual)
                action, code = "copy", "COPY_OK"
            elif self.mode == "hardlink":
                os.link(src, actual)
                action, code = "link", "LINK_OK"
            elif self._same_device(src, actual.parent):
                os.replace(src, actual)
                action, code = "move", "MOVE_OK"
            else:
                shutil.move(str(src), str(actual))
                action, code = "move", "MOVE_OK"

            got = actual.stat().st_size
            result.size = got  # 电影计划没有预期大小，以落盘结果为准
            if expected_size and got != expected_size:
                raise OSError(f"目标大小不符：期望 {expected_size}，实际 {got}")
            with contextlib.suppress(OSError):
                os.chmod(actual, self.file_mode)
            self._chown(actual)
            result.action = action
            result.status = state
            self._used.add(result.actual)
            self.logger.event(
                code if state == "OK" else "VERSION_ADDED",
                ("已新增版本" if state == "VERSION_ADDED" else "已落盘") + f"（{action}）",
                src=source, dst=result.actual, size=got,
            )
            if state == "VERSION_ADDED":
                self.logger.event("VERSION_ADDED", note, src=source, dst=result.actual)
        except OSError as exc:
            result.status = "ERROR"
            result.reason = f"{type(exc).__name__}: {exc}"
            with contextlib.suppress(OSError):
                if actual.exists() and self.mode != "copy":
                    actual.unlink()  # 中断/失败时删残留，源文件保留
            self.logger.error("ERROR", result.reason, src=source, dst=result.actual)
        return result

    # ------------------------------------------------------------ 整条目

    def _planned_files(self, plan: analyze.EntryPlan) -> list[tuple[str, str, int, str]]:
        """(source, logical_target, size, 类别)。视频 → 字幕 → 花絮。"""
        items: list[tuple[str, str, int, str]] = []
        if plan.movie_target and plan.movie_source:
            items.append((plan.movie_source, plan.movie_target, 0, "video"))
        for ep in plan.episodes:
            if ep.target:
                items.append((ep.source, ep.target, ep.size, "video"))
        for sub in plan.subs:
            if sub.target:
                items.append((sub.source, sub.target, sub.size, "subtitle"))
        item_dir = self.item_dir_logical(plan)
        for extra in plan.extras:
            subdir = naming.extra_dir(extra.kind)
            name = PurePosixPath(extra.source).name
            target = naming.join(item_dir, subdir, name) if subdir else naming.join(item_dir, name)
            items.append((extra.source, target, extra.size, "extra"))
        return items

    def _prune(self, plan: analyze.EntryPlan) -> None:
        if not self.prune:
            return
        try:
            root = self.source_path(plan.root)
        except PathError:
            return
        if not root.is_dir():
            return
        for base, dirs, _ in os.walk(root, topdown=False):
            for name in dirs:
                with contextlib.suppress(OSError):
                    Path(base, name).rmdir()
        with contextlib.suppress(OSError):
            root.rmdir()

    def execute_entry(self, plan: analyze.EntryPlan, seq: int) -> EntryResult:
        group = report.detail_group(plan)
        res = EntryResult(seq=seq, root=plan.root, kind=plan.kind,
                          title=plan.title, year=plan.year, season=plan.season)
        if group != "auto":
            res.status = "skipped"
            label = "从未见过的问题（需人工确认）" if group == "new" else "需要人工干涉和处理"
            res.notes.append(f"§5.{group} 条目，按既有决策不动：{label}")
            self.logger.warning(
                "SKIP_MANUAL", "按预览报告的决策跳过该条目", seq=seq, root=plan.root, group=group,
            )
            return res

        for source, target, size, _kind in self._planned_files(plan):
            fr = self.place(source, target, size)
            res.files.append(fr)
            if fr.success:
                parts = [p for p in PurePosixPath(fr.actual).parts if p]
                if len(parts) >= 2:
                    d = "/".join(parts[:-1])
                    if d not in res.target_dirs:
                        res.target_dirs.append(d)

        self._prune(plan)
        attempted = [f for f in res.files if f.target]
        real_success = [f for f in res.files if f.status in OK_STATES]
        bad = [f for f in res.files if f.status in {"ERROR", "CONFLICT"}]
        if not attempted:
            res.status = "skipped"
            res.notes.append("没有可落盘的目标文件")
        elif bad:
            res.status = "partial" if real_success else "failed"
        elif real_success:
            res.status = "success"  # SKIP_SAME / SKIPPED 不影响幂等重跑的退出码
        else:
            res.status = "skipped"
        res.notes.extend(plan.notes)
        self.logger.event(
            "ENTRY_DONE", f"条目处理完成：{res.status}",
            seq=seq, root=plan.root, ok=res.changed, total=len(attempted),
        )
        return res


def _stem_ext(name: str) -> tuple[str, str]:
    if "." not in name:
        return name, ""
    stem, ext = name.rsplit(".", 1)
    return stem, "." + ext


def _logical_from_abs(path: Path, libs: dict[str, Path]) -> str:
    for key, lib in libs.items():
        try:
            return naming.join(key, path.relative_to(lib).as_posix())
        except ValueError:
            continue
    return path.as_posix()


def _mode(value: Any, default: int) -> int:
    try:
        return int(str(value), 8) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return default


__all__ = ["Executor", "FileResult", "EntryResult", "PathError", "OK_STATES", "BAD_STATES"]
