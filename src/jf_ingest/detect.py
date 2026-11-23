"""识别：电影 / 剧集，以及季号、集号、花絮。

集号写法（真实数据验证）：
  [01]        方括号纯数字        562 个
  - 01 / .01.  连字符或点分隔      546 个
  S01E01      标准写法            49 个
  EP01                        34 个
  01（裸数字）  标题 + 空格 + 数字   少量（SAIO 系）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import media, textutil

# 半集（17.5）按原数字保留：第 2 组是整数部分，第 3 组捕获 `.5`
EP_SXXEXX = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})[\s._-]?e(\d{1,4})(\.5)?")
EP_CN = re.compile(r"第\s*(\d{1,4})\s*[集话話]")
EP_PREFIX = re.compile(
    r"(?i)(?<![0-9a-z])(?:ep|e)[\s._-]?(\d{1,4})(\.5)?(?![\da-z])"
)
EP_BRACKET = re.compile(r"\[(\d{1,3})(\.5)?(?:v\d)?\]")
EP_DASH = re.compile(r"(?:\s[-–]\s|_)(\d{1,3})(\.5)?(?:v\d)?(?=[\s\[\]._-]|$)")
EP_BARE = re.compile(r"(?<=\s)(\d{1,3})(\.5)?(?=\s*[\[\s]|$)")

# 不是集号的数字
NOT_EPISODE = {264, 265, 480, 576, 720, 1080, 2160, 3840}

EXTRA_WORDS = re.compile(
    r"(?i)(?<![a-z])(nced|ncop|nced\d*|ncop\d*|pv\d*|cm\d*|menu|preview|trailer|"
    r"spot|teaser|sample|scans?|artwork|bonus|extra|sp\d*|special|oad|ova|"
    r"making|interview|opening|ending|ost|soundtrack|cd\d*|vol\.?\d+)(?![a-z])"
)

EXTRA_DIRS = {
    "extra", "extras", "sp", "sps", "special", "specials", "pv", "cm", "menu",
    "menus", "ncop", "nced", "scans", "scan", "artwork", "bonus", "trailers",
    "trailer", "samples", "sample", "ost", "soundtrack", "cds", "cd",
    "behind the scenes", "interviews", "deleted scenes", "featurettes",
    "shorts", "scenes", "fonts", "subs", "subtitles", "sup", "manga",
}


@dataclass
class EpisodeGuess:
    season: int | None = None
    episode: int | None = None
    half: bool = False
    method: str = ""

    @property
    def found(self) -> bool:
        return self.episode is not None


def parse_episode(stem: str) -> EpisodeGuess:
    m = EP_SXXEXX.search(stem)
    if m:
        return EpisodeGuess(int(m.group(1)), int(m.group(2)), bool(m.group(3)), "SxxExx")
    m = EP_CN.search(stem)
    if m:
        return EpisodeGuess(None, int(m.group(1)), False, "第N集")
    m = EP_PREFIX.search(stem)
    if m:
        return EpisodeGuess(None, int(m.group(1)), bool(m.group(2)), "EPxx")
    m = EP_BRACKET.search(stem)
    if m and int(m.group(1)) not in NOT_EPISODE:
        return EpisodeGuess(None, int(m.group(1)), bool(m.group(2)), "[NN]")
    m = EP_DASH.search(stem)
    if m and int(m.group(1)) not in NOT_EPISODE:
        return EpisodeGuess(None, int(m.group(1)), bool(m.group(2)), "-NN")
    m = EP_BARE.search(stem)
    if m and int(m.group(1)) not in NOT_EPISODE:
        return EpisodeGuess(None, int(m.group(1)), bool(m.group(2)), "bare")
    return EpisodeGuess()


def extra_kind(rel_path: str) -> str | None:
    """判断一个视频是不是花絮；返回花絮类型或 None。

    目录名必须「整个」是 extras/sp/pv…，避免把
    `TV+OAD_BDrip_1080P.../` 这种发布说明当成花絮目录。
    文件名则按关键词匹配（`[NCOP]`、`- PV01` 等）。
    """
    parts = rel_path.lower().split("/")[:-1]
    for part in parts:
        base = re.sub(r"[\[\]()（）]", "", part).strip()
        base = re.sub(r"\s+", " ", base)
        if base in EXTRA_DIRS:
            return _kind_from_word(base)
        # 去掉结尾的 s / 数字，兼容 SP / SPs / Extra / Extras
        trimmed = re.sub(r"\d+$", "", base).rstrip("s ").strip()
        if trimmed in EXTRA_DIRS:
            return _kind_from_word(trimmed)

    stem = rel_path.rsplit("/", 1)[-1]
    stem = textutil.strip_known_ext(stem)
    m = EXTRA_WORDS.search(stem)
    if m:
        return _kind_from_word(m.group(1).lower())
    return None


def _kind_from_word(word: str) -> str:
    word = word.lower()
    if word.startswith("nced") or word.startswith("ncop"):
        return "other"
    if word in {"trailer", "trailers"}:
        return "trailer"
    if word.startswith("pv") or word.startswith("cm") or word in {"teaser", "spot"}:
        return "trailer"
    if word in {"interview", "interviews"}:
        return "interview"
    if word in {"scenes", "deleted scenes"}:
        return "deleted"
    if word in {"featurette", "featurettes"}:
        return "featurette"
    if word in {"short", "shorts"}:
        return "short"
    if word in {"menu", "menus"}:
        return "other"
    return "other"


@dataclass
class Analysis:
    """一个顶层条目的识别结果（不含目标路径）。"""

    root: str
    kind: str = "unknown"          # movie / tv / unknown
    title: str = ""
    year: int | None = None
    season: int | None = None
    title_sure: bool = True
    media: list[media.FileInfo] = field(default_factory=list)      # 正片视频
    extras: list[tuple[media.FileInfo, str]] = field(default_factory=list)  # (文件, 花絮类型)
    episodes: dict[str, EpisodeGuess] = field(default_factory=dict)  # rel -> guess
    issues: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    @property
    def episode_numbers(self) -> list[float]:
        nums = []
        for guess in self.episodes.values():
            if guess.found:
                nums.append(float(guess.episode) + (0.5 if guess.half else 0.0))
        return sorted(nums)


def analyze(entry: media.Entry) -> Analysis:
    a = Analysis(root=entry.root)
    videos = entry.videos
    if not videos:
        a.issues.add("NO_VIDEO")
        if entry.archives or entry.files:
            a.issues.add("NOT_MEDIA")
        return a

    for video in videos:
        kind = extra_kind(video.path)
        if kind:
            a.extras.append((video, kind))
        else:
            a.media.append(video)

    # ---- 片名/年份
    a.title, a.title_sure = textutil.guess_title(entry.root)
    a.year = textutil.parse_year(entry.root)

    if not a.media:
        a.kind = "unknown"
        a.issues.add("NO_EPISODE")
        return a

    guesses = {v.path: parse_episode(v.stem) for v in a.media}
    with_ep = {p: g for p, g in guesses.items() if g.found}

    # ---- 电影
    if len(a.media) == 1 and not with_ep:
        a.kind = "movie"
        _finish_movie(a, entry, a.media[0])
        return a

    # ---- 剧集
    if len(a.media) >= 2 and len(with_ep) >= max(2, int(0.6 * len(a.media))):
        a.kind = "tv"
        a.episodes = with_ep
        _finish_tv(a, entry)
        return a

    # ---- 单文件带集号 → 还是当剧集（只有 1 集）
    if len(a.media) == 1 and with_ep:
        a.kind = "tv"
        a.episodes = with_ep
        _finish_tv(a, entry)
        a.notes.append("只有 1 个视频且带集号，按单集剧集处理")
        return a

    # ---- 多视频但没集号
    if len(a.media) >= 2:
        if _looks_like_parts(a.media):
            a.kind = "movie"
            a.issues.add("MULTI_PART")
            _finish_movie(a, entry, a.media[0])
            a.notes.append(f"共 {len(a.media)} 个视频，疑似同一部电影分段")
            return a
        a.kind = "unknown"
        a.issues.add("NO_EPISODE")
        a.notes.append(f"{len(a.media)} 个视频，一个集号都没识别出来")
        return a

    # ---- 兜底
    a.kind = "movie"
    _finish_movie(a, entry, a.media[0])
    return a


def _looks_like_parts(files: list[media.FileInfo]) -> bool:
    """`片名1.mp4`…`片名4.mp4`：共用一个前缀 + 结尾数字。"""
    if len(files) < 2:
        return False
    prefixes = set()
    for f in files:
        m = re.match(r"^(.*?)(\d{1,2})$", f.stem)
        if not m or not m.group(1).strip():
            return False
        prefixes.add(m.group(1).strip().lower())
    return len(prefixes) == 1


def _finish_movie(a: Analysis, entry: media.Entry, video: media.FileInfo) -> None:
    if not a.title_sure or len(a.title) < 2:
        alt, alt_sure = textutil.guess_title(video.name)
        if alt_sure and len(alt) > len(a.title):
            a.title = alt
            a.title_sure = True
            a.notes.append("片名取自文件名（目录名不可靠）")
        elif not a.title:
            a.title = alt
            a.title_sure = alt_sure
    if a.year is None:
        fy = textutil.parse_year(video.name)
        if fy:
            a.year = fy
            a.notes.append("年份取自文件名")
    if not a.year:
        a.issues.add("NO_YEAR")
    if not a.title_sure:
        a.issues.add("TITLE_LOW_CONFIDENCE")


def _finish_tv(a: Analysis, entry: media.Entry) -> None:
    # 文件名里明写的季号优先：SxxExx > 第二季 / 2nd Season / Season 3 > 目录名。
    # 没写才按需求默认第 1 季。
    for video in a.media:
        guess = a.episodes.get(video.path)
        if guess and guess.season is None:
            guess.season = textutil.parse_season(video.name)
    seasons = {g.season for g in a.episodes.values() if g.season is not None}
    if len(seasons) == 1:
        a.season = seasons.pop()
    elif len(seasons) > 1:
        a.season = min(seasons)
        a.issues.add("SEASON_MIXED")
    else:
        root_season = textutil.parse_season(entry.root)
        if root_season is not None:
            a.season = root_season
        else:
            a.season = 1
            a.issues.add("SEASON_ASSUMED_1")

    if not a.year:
        for video in a.media:
            fy = textutil.parse_year(video.name)
            if fy:
                a.year = fy
                a.notes.append("年份取自文件名")
                break

    numbers = a.episode_numbers
    # 半集按原数字当普通集处理（17.5 → E17.5），不再单独标记
    if numbers and (min(numbers) > 1 or max(numbers) > 60):
        a.issues.add("EP_RANGE_SUSPECT")

    seen: dict[tuple[int, float], int] = {}
    for guess in a.episodes.values():
        key = (a.season or 1, float(guess.episode) + (0.5 if guess.half else 0.0))
        seen[key] = seen.get(key, 0) + 1
    if any(count > 1 for count in seen.values()):
        a.issues.add("DUP_RELEASE")

    if numbers:
        ints = {int(n) for n in numbers}
        missing = [n for n in range(min(ints), max(ints) + 1) if n not in ints]
        if missing:
            a.issues.add("GAP_IN_EPISODES")

    if not a.title_sure:
        a.issues.add("TITLE_LOW_CONFIDENCE")
