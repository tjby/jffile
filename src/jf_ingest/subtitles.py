"""字幕配对与命名建议（阶段一：只分析，不改名）。

Jellyfin 真实规则（源码 MediaInfoResolver.GetExternalFiles）：
  - 只看视频所在目录；
  - 字幕主名 == 视频主名，或以「视频主名 + `.`」开头；
  - 语言段不认识时会变成字幕标题，不影响加载。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import detect, media, naming

# 仅用于「可选」的语言映射（阶段一默认关闭）
LANG_MAP = {
    "chs": "zh-CN", "chi": "zh-CN", "sc": "zh-CN", "gb": "zh-CN", "cn": "zh-CN",
    "cht": "zh-TW", "tc": "zh-TW", "big5": "zh-TW", "hk": "zh-TW",
    "jpn": "ja", "jp": "ja", "eng": "en", "en": "en", "kor": "ko", "kr": "ko",
}
FLAGS = {"default", "forced", "foreign", "sdh", "cc", "hi"}

ARCHIVE_HINT = re.compile(r"(?i)(subtitles?|字幕|(?<![a-z])sup(?![a-z])|(?<![a-z])subs?(?![a-z]))")


@dataclass
class SubMatch:
    file: media.FileInfo
    video: str | None = None          # 配上的视频（相对路径）
    tags: list[str] = field(default_factory=list)
    proposed_name: str | None = None  # 视频主名 + 标签 + 扩展名
    flags: list[str] = field(default_factory=list)


@dataclass
class SubReport:
    matches: list[SubMatch] = field(default_factory=list)
    unpaired: list[media.FileInfo] = field(default_factory=list)
    in_subdir: list[SubMatch] = field(default_factory=list)
    unsupported: list[media.FileInfo] = field(default_factory=list)
    archive_hints: list[media.FileInfo] = field(default_factory=list)
    vobsub_pairs: list[tuple[media.FileInfo, media.FileInfo]] = field(default_factory=list)
    mapped_language: bool = False


def _split_tags(sub_stem: str, video_stem: str) -> list[str]:
    rest = sub_stem[len(video_stem):]
    return [t for t in rest.split(".") if t]


def _apply_lang_map(tags: list[str]) -> list[str]:
    out = []
    for tag in tags:
        low = tag.lower()
        if low in FLAGS:
            out.append(low)
        else:
            out.append(LANG_MAP.get(low, tag))
    return out


def analyze(entry: media.Entry, analysis: detect.Analysis, map_language: bool = False) -> SubReport:
    rep = SubReport(mapped_language=map_language)
    if analysis.kind not in {"movie", "tv"}:
        return rep

    # 1) 同目录前缀匹配（和 Jellyfin 一致）
    videos_by_dir: dict[str, list[media.FileInfo]] = {}
    for video in analysis.media:
        videos_by_dir.setdefault(video.dirname, []).append(video)

    used: set[str] = set()
    for video in analysis.media:
        stem = video.stem.lower()
        same_dir = [s for s in entry.subs if s.dirname == video.dirname]
        matched: list[media.FileInfo] = []
        for sub in same_dir:
            if sub.ext in media.SUB_EXT_UNSUPPORTED:
                rep.unsupported.append(sub)
                continue
            low = sub.stem.lower()
            if low == stem or (low.startswith(stem + ".") and len(low) > len(stem) + 1):
                matched.append(sub)
                used.add(sub.path)

        matched.sort(key=lambda s: s.stem.lower())
        for sub in matched:
            tags = [] if sub.ext in media.VOBSUB_EXT else _split_tags(sub.stem, video.stem)
            if map_language and tags:
                tags = _apply_lang_map(tags)
            flags = [t for t in tags if t.lower() in FLAGS]
            rep.matches.append(
                SubMatch(
                    file=sub,
                    video=video.path,
                    tags=tags,
                    proposed_name=naming.subtitle_file(video.stem, tags, sub.ext),
                    flags=flags,
                )
            )

    # 2) 视频目录之外的字幕：按集号尝试归属
    for sub in entry.subs:
        if sub.path in used:
            continue
        if sub.ext in media.SUB_EXT_UNSUPPORTED:
            if sub not in rep.unsupported:
                rep.unsupported.append(sub)
            continue
        if sub.dirname in videos_by_dir:
            continue  # 同目录但配不上，留到第 3 步
        guess = detect.parse_episode(sub.stem)
        target = None
        if guess.found:
            for video in analysis.media:
                vg = analysis.episodes.get(video.path)
                if vg and vg.found and vg.episode == guess.episode and vg.half == guess.half:
                    target = video
                    break
        if target:
            tags = _split_tags(sub.stem, target.stem)
            if map_language and tags:
                tags = _apply_lang_map(tags)
            rep.in_subdir.append(
                SubMatch(
                    file=sub,
                    video=target.path,
                    tags=[t for t in tags if t],
                    proposed_name=naming.subtitle_file(target.stem, tags, sub.ext),
                    flags=[t for t in tags if t.lower() in FLAGS],
                )
            )
            used.add(sub.path)
        else:
            rep.unpaired.append(sub)

    # 3) 同目录但配不上的字幕
    for sub in entry.subs:
        if sub.path in used or sub in rep.unsupported:
            continue
        if sub in rep.unpaired:
            continue
        if sub.dirname not in videos_by_dir:
            continue
        rep.unpaired.append(sub)

    # 4) VobSub 成对
    by_stem: dict[str, dict[str, media.FileInfo]] = {}
    for sub in entry.subs:
        by_stem.setdefault(f"{sub.dirname}|{sub.stem.lower()}", {})[sub.ext] = sub
    for group in by_stem.values():
        if ".idx" in group and ".sub" in group:
            rep.vobsub_pairs.append((group[".idx"], group[".sub"]))

    # 5) 压缩包里的字幕
    for archive in entry.archives:
        if ARCHIVE_HINT.search(archive.name):
            rep.archive_hints.append(archive)

    return rep
