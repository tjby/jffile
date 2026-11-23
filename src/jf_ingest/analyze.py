"""把识别结果组装成「条目计划」：目标路径、字幕建议、待确认标记。"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import detect, media, naming, subtitles


@dataclass
class EpisodePlan:
    source: str
    season: int
    episode: int
    half: bool
    target: str
    size: int
    method: str


@dataclass
class SubPlan:
    source: str
    target: str
    video: str | None
    tags: list[str]
    status: str  # same_dir / subdir / unpaired / unsupported / archive
    size: int


@dataclass
class ExtraPlan:
    source: str
    kind: str
    size: int
    target: str | None = None


@dataclass
class EntryPlan:
    root: str
    kind: str
    title: str
    year: int | None
    season: int | None
    confidence: str
    issues: list[str]
    notes: list[str]
    episodes: list[EpisodePlan] = field(default_factory=list)
    movie_target: str | None = None
    movie_source: str | None = None
    subs: list[SubPlan] = field(default_factory=list)
    extras: list[ExtraPlan] = field(default_factory=list)
    media_count: int = 0
    total_size: int = 0
    # FR-17：源条目的完整文件清单（相对源根），apply 写「原始结构」要用
    source_files: list[str] = field(default_factory=list)

    @property
    def subtitle_count(self) -> int:
        return len([s for s in self.subs if s.status in {"same_dir", "subdir"}])

    @property
    def ep_range(self) -> str:
        if not self.episodes:
            return ""
        eps = [e.episode + (0.5 if e.half else 0) for e in self.episodes]
        return f"{_fmt(min(eps))}-{_fmt(max(eps))}"


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _attachment_kind(f: media.FileInfo) -> str | None:
    """图片/音频附件的 Jellyfin 归类（FR-14）。"""
    if f.ext in media.IMAGE_EXT:
        parts = [p.lower() for p in f.dirname.split("/") if p]
        if "backdrops" in parts or "backdrop" in parts:
            return "backdrop"
        return "cover"
    if f.ext in media.AUDIO_EXT:
        return "theme" if f.stem.lower() == "theme" else "audio_extra"
    return None


# 需要人看的标记（决定置信度）
_NEEDS_ATTENTION = {
    "DUP_RELEASE", "EP_RANGE_SUSPECT",
    "MULTI_PART", "NO_EPISODE", "SEASON_MIXED",
}
_BLOCKING = {"TITLE_LOW_CONFIDENCE", "NO_VIDEO", "NOT_MEDIA"}


def _confidence(analysis: detect.Analysis) -> str:
    if analysis.kind == "unknown":
        return "low"
    issues = set(analysis.issues)
    if issues & _BLOCKING:
        return "low"
    if issues & _NEEDS_ATTENTION:
        return "medium"
    return "high"


def build(entry: media.Entry, cfg: dict) -> EntryPlan:
    analysis = detect.analyze(entry)
    report = subtitles.analyze(
        entry,
        analysis,
        map_language=bool(cfg.get("subtitle", {}).get("map_language", False)),
    )

    plan = EntryPlan(
        root=entry.root,
        kind=analysis.kind,
        title=analysis.title,
        year=analysis.year,
        season=analysis.season,
        confidence=_confidence(analysis),
        issues=sorted(analysis.issues),
        notes=list(analysis.notes),
        media_count=len(analysis.media),
        total_size=entry.total_size,
        source_files=[f.path for f in entry.files],
    )

    movies_dir = "movies"
    tv_dir = "tvshows"  # 报告里用相对根路径，避免泄露绝对路径

    # 每个视频的目标文件名（字幕要跟着它改名，而不是跟着源文件名）
    targets: dict[str, tuple[str, str]] = {}  # 源相对路径 -> (目标目录, 目标主名)

    if analysis.kind == "movie" and analysis.media:
        video = analysis.media[0]
        folder = naming.movie_folder(analysis.title, analysis.year)
        name = naming.movie_file(analysis.title, analysis.year, video.ext)
        plan.movie_target = naming.join(movies_dir, folder, name)
        plan.movie_source = video.path
        targets[video.path] = (naming.join(movies_dir, folder), name[: -len(video.ext)])
        if len(analysis.media) > 1:
            plan.notes.append(
                f"另有 {len(analysis.media) - 1} 个视频（疑似分段），阶段一不处理"
            )

    if analysis.kind == "tv":
        season = analysis.season or 1
        show = naming.show_folder(analysis.title, analysis.year)
        for video in analysis.media:
            guess = analysis.episodes.get(video.path)
            if not guess or not guess.found:
                continue
            ep_season = guess.season if guess.season is not None else season
            name = naming.episode_file(
                analysis.title, analysis.year, ep_season, int(guess.episode), video.ext,
                half=guess.half,
            )
            target = naming.join(
                tv_dir, show, naming.season_folder(ep_season), name
            )
            plan.episodes.append(
                EpisodePlan(
                    source=video.path,
                    season=ep_season,
                    episode=int(guess.episode),
                    half=guess.half,
                    target=target,
                    size=video.size,
                    method=guess.method,
                )
            )
            targets[video.path] = (
                naming.join(tv_dir, show, naming.season_folder(ep_season)),
                name[: -len(video.ext)],
            )
        plan.episodes.sort(key=lambda e: (e.season, e.episode, e.half))

    used_names: dict[str, set[str]] = {}

    def add_sub(match: subtitles.SubMatch, status: str) -> None:
        src = match.file.path
        target = ""
        if match.video and match.video in targets:
            folder, stem = targets[match.video]
            name = naming.subtitle_file(stem, match.tags, match.file.ext)
            bucket = used_names.setdefault(folder, set())
            if name in bucket:
                name = naming.subtitle_file(stem, match.tags + ["2"], match.file.ext)
            bucket.add(name)
            target = f"{folder}/{name}"
        plan.subs.append(
            SubPlan(
                source=src,
                target=target,
                video=match.video,
                tags=match.tags,
                status=status,
                size=match.file.size,
            )
        )

    for match in report.matches:
        add_sub(match, "same_dir")
    for match in report.in_subdir:
        add_sub(match, "subdir")
    for sub in report.unpaired:
        plan.subs.append(SubPlan(sub.path, "", None, [], "unpaired", sub.size))
    for sub in report.unsupported:
        plan.subs.append(SubPlan(sub.path, "", None, [], "unsupported", sub.size))
    for archive in report.archive_hints:
        plan.subs.append(SubPlan(archive.path, "", None, [], "archive", archive.size))

    for video, kind in analysis.extras:
        plan.extras.append(ExtraPlan(video.path, kind, video.size))
    # FR-14：图片/主题曲跟宿主条目走；放库根会被 Jellyfin 当独立媒体
    for att in entry.files:
        kind = _attachment_kind(att)
        if kind:
            plan.extras.append(ExtraPlan(att.path, kind, att.size))

    if report.in_subdir:
        plan.issues.append("SUBS_IN_SUBDIR")
    if report.unpaired:
        plan.issues.append("SUBS_UNPAIRED")
    if report.unsupported:
        plan.issues.append("SUB_FORMAT_UNSUPPORTED")
    if report.archive_hints:
        plan.issues.append("SUBS_IN_ARCHIVE")
    if analysis.extras and analysis.kind == "unknown":
        plan.issues.append("EXTRAS_AMBIGUOUS")
    plan.issues = sorted(set(plan.issues))

    return plan
