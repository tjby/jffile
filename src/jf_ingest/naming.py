"""目标路径生成（纯函数，不碰磁盘）。

Jellyfin 规则（已核对官方文档与源码）：
  movies/<片名 (年份)>/<片名 (年份)>.ext
  tvshows/<剧名 (年份)>/Season 01/<剧名 (年份)> S01E01.ext
"""

from __future__ import annotations

import re

from .textutil import sanitize, truncate_utf8

# 名字里给文件名留的字节预算（Linux 单个文件名上限 255 字节）
NAME_BUDGET = 240


def _numbered(prefix: str, value: int) -> str:
    return f"{prefix}{value:02d}"


def display_name(title: str, year: int | None) -> str:
    """`片名 (年份)`；没有年份就不写，绝不出现空括号。"""
    t = sanitize(title) or "未命名"
    t = truncate_utf8(t, NAME_BUDGET - 8)
    return f"{t} ({year})" if year else t


def movie_folder(title: str, year: int | None) -> str:
    return display_name(title, year)


def movie_file(title: str, year: int | None, ext: str, version: str | None = None) -> str:
    base = display_name(title, year)
    if version:
        base = f"{base} - {sanitize(version)}"
    return f"{base}{ext}"


def show_folder(title: str, year: int | None) -> str:
    return display_name(title, year)


def season_folder(season: int) -> str:
    return f"Season {season:02d}"


def episode_tag(season: int, episode: int, half: bool = False) -> str:
    """半集保留原数字：E17.5，不改成 E17 / E18。"""
    return f"S{season:02d}E{episode:02d}" + (".5" if half else "")


def episode_file(
    title: str,
    year: int | None,
    season: int,
    episode: int,
    ext: str,
    episode_title: str | None = None,
    half: bool = False,
) -> str:
    base = f"{display_name(title, year)} {episode_tag(season, episode, half)}"
    if episode_title:
        base = f"{base} {sanitize(episode_title)}"
    return truncate_utf8(base, NAME_BUDGET - len(ext)) + ext


def subtitle_file(video_stem: str, tags: list[str], ext: str) -> str:
    """字幕名 = 视频主名 + `.标签` + 扩展名（分隔符只能是点）。"""
    suffix = "".join(f".{sanitize(t)}" for t in tags if t)
    return truncate_utf8(video_stem + suffix, NAME_BUDGET - len(ext)) + ext


def extra_dir(kind: str) -> str:
    """花絮子目录名；空串表示放条目根目录（封面/主题曲）。"""
    return {
        "trailer": "trailers",
        "behindthescenes": "behind the scenes",
        "deleted": "deleted scenes",
        "interview": "interviews",
        "scene": "scenes",
        "short": "shorts",
        "featurette": "featurettes",
        "backdrop": "backdrops",
        "audio_extra": "extras",
        "cover": "",
        "theme": "",
        "other": "extras",
    }.get(kind, "extras")


def join(*parts: str) -> str:
    return "/".join(p.strip("/") for p in parts if p)


def version_label(width: int | None = None, height: int | None = None) -> str | None:
    """按分辨率生成多版本标签（无 ffprobe 时只在有尺寸信息时用）。"""
    if width and height:
        if height >= 2000:
            return "2160p"
        if height >= 1000:
            return "1080p"
        if height >= 700:
            return "720p"
        return f"{height}p"
    return None


_RES_LABEL = re.compile(r"(?i)(?<![a-z0-9])(2160p|4k|1080p|720p|480p|576p)(?![a-z0-9])")
_QUAL_LABEL = re.compile(
    r"(?i)(?<![a-z0-9])(hdr|dv|dolby.?vision|imax|directors? ?cut|director|"
    r"extended|uncut|加长版|导演剪辑版)(?![a-z0-9])"
)
_DISC_LABEL = re.compile(r"(?i)(?<![a-z0-9])(cd\d|dvd\d|part\d|pt\d|disc\d|disk\d)(?![a-z0-9])")


def version_from_name(name: str) -> str | None:
    """从源文件名提取版本标签：分辨率 > 画质 > 版本词 > 碟/段。

    服务器没有 ffprobe，所以只从文件名取；都没有就返回 None，
    由执行器回退成 v2/v3 或 (2)。
    """
    for pattern in (_RES_LABEL, _QUAL_LABEL, _DISC_LABEL):
        m = pattern.search(name)
        if m:
            return sanitize(m.group(1)).lower()
    return None


def with_version(stem: str, ext: str, label: str) -> str:
    """`片名 (2023)` + `2160p` → `片名 (2023) - 2160p.mkv`。"""
    return f"{stem} - {sanitize(label)}{ext}"
