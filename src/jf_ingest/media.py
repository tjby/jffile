"""基础类型与常量。

扩展名以 Jellyfin 源码为准（2026-11 master）：
  - 视频：NamingOptions.VideoFileExtensions
  - 字幕：NamingOptions.SubtitleFileExtensions（.idx 单独按 VobSub 配对）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 扩展名

VIDEO_EXT = {
    ".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv",
    ".webm", ".iso", ".mpg", ".mpeg", ".m4v", ".rmvb", ".rm", ".vob",
    ".3gp", ".ogv", ".divx", ".asf",
}

# Jellyfin 真正会读的外挂字幕
SUB_EXT = {".ass", ".mks", ".sami", ".smi", ".srt", ".ssa", ".sub", ".sup", ".vtt"}
# 需要成对处理的 VobSub
VOBSUB_EXT = {".idx"}
# Jellyfin 不认，需要提示
SUB_EXT_UNSUPPORTED = {".ttml", ".dfxp", ".lrc"}

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".avif", ".webp", ".bmp", ".tif", ".tiff"}
AUDIO_EXT = {".flac", ".mp3", ".m4a", ".aac", ".wav", ".ape", ".ogg", ".opus", ".wma", ".dts"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".xz", ".bz2", ".001"}
JUNK_EXT = {
    ".torrent", ".!qb", ".part", ".tmp", ".aria2", ".crdownload", ".downloading",
    ".uploading", ".xltd", ".exe", ".docx", ".log", ".txt", ".cue", ".nfo",
    ".xml", ".json", ".url", ".lnk", ".db", ".ini",
}

# 这些图片名会被 Jellyfin 当封面/背景
COVER_NAMES = {"cover", "folder", "poster", "fanart", "backdrop", "logo", "banner",
               "thumb", "landscape", "clearart", "disc", "clearlogo"}

KIND_LABEL_ZH = {"movie": "电影", "tv": "剧集", "unknown": "无法判断"}

# ---------------------------------------------------------------- 数据模型


@dataclass
class FileInfo:
    """一个文件（来自磁盘或清单）。path 是相对源目录的 POSIX 路径。"""

    path: str
    size: int
    mtime: float = 0.0

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def dirname(self) -> str:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""

    @property
    def ext(self) -> str:
        name = self.name
        if "." not in name:
            return ""
        return "." + name.rsplit(".", 1)[-1].lower()

    @property
    def stem(self) -> str:
        name = self.name
        ext = self.ext
        return name[: -len(ext)] if ext else name


@dataclass
class Entry:
    """一个顶层条目：源目录下的一个文件夹（或一个散落文件）。"""

    root: str
    files: list[FileInfo] = field(default_factory=list)

    def by_ext(self, exts: set[str]) -> list[FileInfo]:
        return [f for f in self.files if f.ext in exts]

    @property
    def videos(self) -> list[FileInfo]:
        return self.by_ext(VIDEO_EXT)

    @property
    def subs(self) -> list[FileInfo]:
        return [f for f in self.files if f.ext in SUB_EXT or f.ext in VOBSUB_EXT]

    @property
    def images(self) -> list[FileInfo]:
        return self.by_ext(IMAGE_EXT)

    @property
    def archives(self) -> list[FileInfo]:
        return self.by_ext(ARCHIVE_EXT)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


# ---------------------------------------------------------------- 问题代码

# 中文短名：报告里「代码（中文名）」同时出现，保留代码方便跨版本对照
ISSUE_LABEL = {
    "NO_YEAR": "缺少年份",
    "SEASON_ASSUMED_1": "未标季号，按第 1 季",
    "SEASON_MIXED": "季号冲突",
    "EP_RANGE_SUSPECT": "集号范围异常",
    "HALF_EPISODE": "半集（历史标记；现已按原数字入库）",
    "DUP_RELEASE": "同一集有两套版本",
    "GAP_IN_EPISODES": "集号有缺口",
    "NO_EPISODE": "找不到集号",
    "MULTI_PART": "疑似电影分段",
    "TITLE_LOW_CONFIDENCE": "片名不可靠",
    "SUBS_IN_ARCHIVE": "字幕在压缩包里",
    "SUBS_IN_SUBDIR": "字幕在别的目录",
    "SUBS_UNPAIRED": "字幕配不上视频",
    "SUB_FORMAT_UNSUPPORTED": "字幕格式不支持",
    "EXTRAS_AMBIGUOUS": "花絮归属不明",
    "NO_VIDEO": "没有视频",
    "NOT_MEDIA": "不是影视内容",
}

# 这些是「需要人看」的标记，会汇总进报告的「待确认」章节
ISSUES = {
    "NO_YEAR": "源里找不到年份，目标名将不带年份",
    "SEASON_ASSUMED_1": "没有季号信息，按第 1 季处理",
    "SEASON_MIXED": "同名文件里出现多个季号，无法统一",
    "EP_RANGE_SUSPECT": "集号不从 1 开始或跨度异常，可能是跨季连续编号或只上传了后半段",
    "HALF_EPISODE": "历史标记：现在 .5 集按原数字当普通集入库（E17.5）",
    "DUP_RELEASE": "同一集有多个视频文件（可能是两套 release）",
    "GAP_IN_EPISODES": "集号不连续",
    "NO_EPISODE": "多个视频但一个集号都没识别出来",
    "MULTI_PART": "多个视频无集号，可能是同一部电影的分段",
    "TITLE_LOW_CONFIDENCE": "片名解析不可靠，建议人工确认",
    "SUBS_IN_ARCHIVE": "字幕在压缩包里，Jellyfin 看不到（未解压）",
    "SUBS_IN_SUBDIR": "字幕不在视频所在目录，Jellyfin 不会扫描（未搬运）",
    "SUBS_UNPAIRED": "有字幕文件配不到任何视频",
    "SUB_FORMAT_UNSUPPORTED": "字幕格式 Jellyfin 不支持",
    "EXTRAS_AMBIGUOUS": "花絮无法确定归属",
    "NO_VIDEO": "没有任何视频文件",
    "NOT_MEDIA": "看起来不是影视内容",
}


def issue_label(code: str) -> str:
    """报告里显示成 `CODE（中文名）`，代码保留给脚本/跨版本对照。"""
    return f"{code}（{ISSUE_LABEL.get(code, '其它')}）"


def issue_text(code: str) -> str:
    """中文短名 + 详细说明。"""
    name = ISSUE_LABEL.get(code, "其它")
    detail = ISSUES.get(code, "")
    return f"{name}：{detail}" if detail else name


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False
