"""在真实清单上试探「自动识别」的可行边界（只读分析，不移动任何文件）。

读取 doc/inventory/upload-files.tsv，对 204 个顶层条目逐个尝试：
  - 判断电影 / 剧集
  - 猜测片名、年份、季号、集号
  - 标出需要人工确认的情况

输出：doc/inventory/parse-probe.md + parse-probe.tsv

用法: python tools/probe_parse.py
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path

TSV = Path("doc/inventory/upload-files.tsv")
OUT_MD = Path("doc/inventory/parse-probe.md")
OUT_TSV = Path("doc/inventory/parse-probe.tsv")

VIDEO_EXT = {
    ".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv",
    ".webm", ".iso", ".mpg", ".mpeg", ".rmvb", ".rm", ".vob", ".m4v",
}
SUB_EXT = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".smi", ".ttml", ".dfxp", ".sup"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".avif", ".webp", ".bmp", ".gif"}
AUDIO_EXT = {".flac", ".mp3", ".m4a", ".aac", ".wav", ".ape", ".ogg", ".opus"}

# 明显的“非正片”标记（花絮、菜单、预告、特典）
EXTRA_TOKENS = re.compile(
    r"(?i)(?<![a-z])(nced|ncop|nc\s?ed|nc\s?op|pv\d*|cm\d*|menu|preview|trailer|"
    r"spot|teaser|sample|scans?|artwork|bonus|extra|sp\d*|special|oad|ova|"
    r"making|interview|opening|ending|ost|soundtrack|cd\d*|vol\.?\d+)(?![a-z])"
)

# 元数据 token：出现在方括号里时说明不是片名
META_TOKEN = re.compile(
    r"(?i)^(\d{1,3}(\.\d+)?|"
    r"\d{3,4}[pi]|8bit|10bit|hi10p|ma10p|"
    r"hevc|x265|x264|h\.?264|h\.?265|avc|av1|vp9|"
    r"aac|flac|ac3|dts|ddp?\d?(\.\d)?|opus|mp3|truehd|atmos|"
    r"web-?dl|webrip|web|bdrip|bd|bluray|blu-ray|dvdrip|hdrip|hdtv|remux|"
    r"limited|unrated|extended|proper|repack|internal|remastered|"
    r"chs|cht|gb|big5|sc|tc|jpsc|jp|jpn|eng|chi|zho|kr|kor|"
    r"简体|繁體|繁体|简繁|简繁日|简日|繁日|内封|内嵌|外挂|中字|中日|双语|"
    r"fin|tv|全集|合集|movie|raws?|raw|mkv|mp4|avi|hd\d{3,4}p|"
    r"[0-9a-f]{6,}|v\d|rev\d?)$"
)
# 出现 2 个以上即判定为“元数据括号”，如 "WebRip 1080p HEVC-10bit AAC"
META_WORD = re.compile(
    r"(?i)(?<![a-z])(4k|8k|1080[pi]|720p|2160p|480p|1920x1080|3840x2160|"
    r"8bit|10bit|hi10p|ma10p|hevc|x265|x264|h264|h265|avc|av1|vp9|"
    r"aac|flac|ac3|eac3|dts|ddp?|opus|mp3|truehd|atmos|lpcm|"
    r"web-?dl|webrip|web|bdrip|bd|bluray|blu-ray|dvdrip|hdrip|hdtv|remux|tvrip|"
    r"chs|cht|gb|big5|jpsc|jpn|eng|chi|zho|fin|tv|mkv|mp4|avi|srt|ass|sup)(?![a-z])"
)
CN_META = re.compile(
    r"(简体|繁體|繁体|简繁|简日|繁日|简繁日|内封|内嵌|外挂|中字|中日|双语|"
    r"国语|粤语|日语|英语|合集|全集|特典|映像|修复版|未删减|导演剪辑|"
    r"首发|中文字幕|给力剧情)"
)
URL_LIKE = re.compile(r"(?i)www\.|\.(com|net|org|cc|me|info)\b|sexinsex|18p2p|bttop|bt\s*首发|梦幻天堂|龙网|优优|crazecat|download|下载加速器")
GROUP_HINT = re.compile(
    r"(?i)(raw|raws|sub|subs|house|studio|team|works|ani|lilith|neko|sakurato|"
    r"lolihouse|dmg|sumisora|lk?sub|ktxp|dmhy|vcb|sao|sao-|orion|milk|prejudice|"
    r"comicat|kiss|hy|dbd|ly|h-enc|mbr|7³|343)"
)
KNOWN_EXT = VIDEO_EXT | SUB_EXT | IMG_EXT | AUDIO_EXT | {
    ".torrent", ".7z", ".zip", ".rar", ".exe", ".docx", ".txt", ".log",
    ".cue", ".xltd", ".nfo", ".xml", ".json", ".part", ".tmp", ".gif",
}

SEASON_DIR = re.compile(
    r"(?i)(?<![a-z])(?:s(\d{1,2})(?![\deex]))|season\s*(\d{1,2})|第\s*([一二三四五六七八九十\d]{1,3})\s*季"
)
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
ORDINAL = re.compile(r"(?i)(\d{1,2})(?:st|nd|rd|th)\s*season")

EP_BRACKET = re.compile(r"\[(\d{1,3})(\.5)?(?:v\d)?\]")
EP_SXXEXX = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})[\s._-]?e(\d{1,4})")
EP_DASH = re.compile(r"(?:\s[-–]\s|_)(\d{1,3})(\.5)?(?:v\d)?(?=[\s\[\]._-]|$)")
EP_CN = re.compile(r"第\s*(\d{1,4})\s*[集话話]")
EP_PREFIX = re.compile(r"(?i)(?<![a-z])(?:ep|e)[\s._-]?(\d{1,4})(?![0-9])")
EP_BARE = re.compile(r"(?<=\s)(\d{1,3})(\.5)?(?=\s*[\[\s]|$)")


@dataclass
class Entry:
    root: str
    files: list[tuple[str, int]] = field(default_factory=list)  # (relpath, size)

    @property
    def videos(self) -> list[tuple[str, int]]:
        return [f for f in self.files if Path(f[0]).suffix.lower() in VIDEO_EXT]

    @property
    def subs(self) -> list[tuple[str, int]]:
        return [f for f in self.files if Path(f[0]).suffix.lower() in SUB_EXT]

    @property
    def images(self) -> list[tuple[str, int]]:
        return [f for f in self.files if Path(f[0]).suffix.lower() in IMG_EXT]

    @property
    def audio(self) -> list[tuple[str, int]]:
        return [f for f in self.files if Path(f[0]).suffix.lower() in AUDIO_EXT]


def load_entries() -> list[Entry]:
    entries: dict[str, Entry] = {}
    for line in TSV.read_text(encoding="utf-8").splitlines():
        if not line or line[0] not in "fdl":
            continue
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        typ, size, _mtime, rel = parts
        if typ != "f":
            continue
        root = rel.split("/", 1)[0]
        entries.setdefault(root, Entry(root)).files.append((rel, int(size)))
    return sorted(entries.values(), key=lambda e: e.root)


def strip_ext(name: str) -> str:
    base = name.rsplit(".", 1)
    if len(base) == 2 and ("." + base[1].lower()) in KNOWN_EXT:
        return base[0]
    return name


def segments(name: str) -> list[str]:
    """把方括号/圆括号/书名号内容拆出来，其余当作普通文本。"""
    out: list[str] = []
    for m in re.finditer(r"\[([^\]]*)\]|\(([^)]*)\)|【([^】]*)】|([^\[\]()【】]+)", name):
        val = m.group(1) or m.group(2) or m.group(3) or m.group(4) or ""
        val = val.strip()
        if val:
            out.append(val)
    return out


def tokens(name: str) -> list[tuple[str, str, int]]:
    """按顺序返回 (类型, 词, 文本段编号) —— 括号段整体算一个词，普通文本按 . _ 拆词。"""
    out: list[tuple[str, str, int]] = []
    pos = 0
    run = 0
    for m in re.finditer(r"\[([^\]]*)\]|\(([^)]*)\)|【([^】]*)】", name):
        pre = name[pos:m.start()]
        if pre.strip():
            for t in re.split(r"[._]+", pre):
                t = t.strip()
                if t:
                    out.append(("text", t, run))
            run += 1
        val = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if val:
            out.append(("br", val, -1 - len(out)))
        pos = m.end()
    tail = name[pos:]
    if tail.strip():
        for t in re.split(r"[._]+", tail):
            t = t.strip()
            if t:
                out.append(("text", t, run))
    return out




def is_meta(seg: str) -> bool:
    s = seg.strip()
    if not s:
        return True
    if META_TOKEN.match(s.replace(" ", "")):
        return True
    if re.fullmatch(r"(?i)[\d.\-–~ ]+", s):
        return True
    if re.fullmatch(r"(?i)\d{1,3}(-\d{1,3})?\s*(fin|tv|全集)?", s):
        return True
    if URL_LIKE.search(s):
        return True
    if CN_META.search(s):
        return True
    if re.fullmatch(r"(?i)\d{6,8}", s):  # 形如 20201004 的日期
        return True
    if len(META_WORD.findall(s)) >= 2 and not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", s):
        return True
    return False


def clean_piece(s: str) -> str:
    s = s.strip(" ._-,;:·|")
    s = re.sub(r"(?i)^\[[^\]]*\]", "", s).strip()
    return s


def strip_tail_tags(title: str) -> str:
    """去掉片名尾部残留的年份、语言、分辨率等尾巴。"""
    t = title.strip()
    for _ in range(4):
        before = t
        t = re.sub(r"(?i)\s*[-–]?\s*(19|20)\d{2}\s*$", "", t)
        t = re.sub(r"(?i)\s+(中文字幕|中字|双语|国语|粤语|日语|英语|官方[^\s]*中字?|"
                   r"hd\d{3,4}p|\d{3,4}p|bluray|bdrip|webrip|web-dl|remux)\s*$", "", t)
        t = t.strip(" ._-,;:·|")
        if t == before:
            break
    return t



def split_at_names(name: str) -> str:
    """`道@sexinsex.net@美女海滩` → `美女海滩`（去掉站点前缀）。"""
    if "@" not in name:
        return name
    parts = [p for p in name.split("@") if p.strip()]
    kept = [p for p in parts if not URL_LIKE.search(p) and p.strip().lower() not in {"tto", "道", "第一流氓"}]
    if kept:
        return kept[-1]
    return parts[-1]


def guess_title(name: str) -> tuple[str, bool]:
    """返回 (片名猜测, 是否可信)。"""
    name = split_at_names(strip_ext(name))
    name = re.sub(r"(?i)(18p2p|sexinsex|第一流氓|tto|bttop|bt\s*首发)", "", name)
    tk = tokens(name)
    if not tk:
        return "", False

    starts_with_bracket = name.lstrip().startswith("[")
    cands: list[tuple[int, str, str, int]] = []
    skipped_first_bracket = False
    for idx, (kind, val, run) in enumerate(tk):
        if is_meta(val):
            continue
        val = clean_piece(val)
        if not val:
            continue
        if kind == "br" and idx == 0 and starts_with_bracket and not skipped_first_bracket and len(tk) > 1:
            skipped_first_bracket = True
            continue
        cands.append((idx, kind, val, run))

    if not cands:
        return clean_piece(tk[0][1]), False

    pos, kind, val, run = cands[0]
    title = val

    # 普通文本段（点/下划线分隔）把同一段里的词拼回去：In.the.Sign.of.the.Taurus.1974
    if kind == "text":
        parts = [val]
        for k2, v2, r2 in tk[pos + 1:]:
            if r2 != run:
                break
            if re.fullmatch(r"(19|20)\d{2}", v2) or is_meta(v2):
                break
            parts.append(v2)
        title = " ".join(parts)

    title = strip_season_marker(strip_tail_tags(title))
    sure = True
    if len(title) < 3 and not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", title):
        sure = len(cands) == 1 and len(title) >= 2
    if URL_LIKE.search(title):
        sure = False
    return title, sure




def strip_season_marker(title: str) -> str:
    """把片名尾部的 S02 / 2nd Season / 第三季 去掉（季号单独存在）。"""
    t = title.strip()
    t = re.sub(r"(?i)\s*[-–]?\s*(?:s|season)\s*\d{1,2}\s*$", "", t)
    t = re.sub(r"(?i)\s*[-–]?\s*\d{1,2}(?:st|nd|rd|th)\s+season\s*$", "", t)
    t = re.sub(r"\s*第\s*[一二三四五六七八九十\d]{1,3}\s*季\s*$", "", t)
    return t.strip(" ._-,;:·|")


def parse_season_dir(name: str) -> int | None:
    m = SEASON_DIR.search(name)
    if m:
        for g in m.groups():
            if g:
                if g.isdigit():
                    return int(g)
                return CN_NUM.get(g)
    m = ORDINAL.search(name)
    if m:
        return int(m.group(1))
    return None


def detect_episode(stem: str) -> tuple[int | None, str, float | None, int | None]:
    """返回 (集号, 方法, 半集, 显式季号)。"""
    m = EP_SXXEXX.search(stem)
    if m:
        return int(m.group(2)), "SxxExx", None, int(m.group(1))
    m = EP_CN.search(stem)
    if m:
        return int(m.group(1)), "第N集", None, None
    m = EP_PREFIX.search(stem)
    if m:
        return int(m.group(1)), "EPnn", None, None
    m = EP_BRACKET.search(stem)
    if m:
        return int(m.group(1)), "[NN]", (0.5 if m.group(2) else None), None
    m = EP_DASH.search(stem)
    if m:
        return int(m.group(1)), "- NN", (0.5 if m.group(2) else None), None
    m = EP_BARE.search(stem)
    if m and int(m.group(1)) not in {264, 265, 480, 576, 720, 1080, 2160}:
        return int(m.group(1)), "bare NN", (0.5 if m.group(2) else None), None
    return None, "", None, None


def parse_year(name: str) -> int | None:
    years = [int(y) for y in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?![\dpxX×])", name)]
    years = [y for y in years if 1900 <= y <= 2100]
    if not years:
        return None
    # 取最靠后的（通常是发行年）
    return years[-1]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@dataclass
class Analysis:
    root: str
    kind: str = "UNKNOWN"          # movie / tv / unknown
    confidence: str = "low"        # high / medium / low
    title: str = ""
    year: int | None = None
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    half: int = 0
    extras: int = 0
    nvid: int = 0
    nsub: int = 0
    nimg: int = 0
    naudio: int = 0
    flags: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def ep_range(self) -> str:
        if not self.episodes:
            return ""
        return f"{min(self.episodes)}-{max(self.episodes)}"


def analyze(entry: Entry) -> Analysis:
    a = Analysis(root=entry.root)
    vids = entry.videos
    a.nvid = len(vids)
    a.nsub = len(entry.subs)
    a.nimg = len(entry.images)
    a.naudio = len(entry.audio)

    if not vids:
        a.kind = "unknown"
        a.flags.append("NO_VIDEO")
        a.note = "无视频文件（游戏/压缩包/图片）"
        return a

    root_season = parse_season_dir(entry.root)
    a.title, title_ok = guess_title(entry.root)
    a.year = parse_year(entry.root)

    media: list[tuple[str, int | None, str, float | None, int | None]] = []
    for rel, size in vids:
        stem = strip_ext(rel.rsplit("/", 1)[-1])
        if EXTRA_TOKENS.search(stem) and not EP_SXXEXX.search(stem):
            a.extras += 1
            continue
        ep, method, half, season_in_name = detect_episode(stem)
        media.append((rel, ep, method, half, season_in_name))

    methods = {m for _, ep, m, _, _ in media if ep is not None}
    with_ep = [x for x in media if x[1] is not None]

    if len(with_ep) >= 2 and len(with_ep) >= 0.6 * len(media):
        a.kind = "tv"
        # 季号：文件名里的显式季号优先，其次目录名；默认 1
        seasons = {x[4] for x in with_ep if x[4] is not None}
        if len(seasons) == 1:
            a.season = seasons.pop()
        elif len(seasons) > 1:
            a.flags.append("MULTI_SEASON_FILES")
            a.season = min(seasons)
        else:
            a.season = root_season or 1
            if root_season is None:
                a.flags.append("SEASON_ASSUMED_1")
        a.episodes = sorted({x[1] for x in with_ep})
        a.half = sum(1 for x in with_ep if x[3])
        if a.half:
            a.flags.append("HALF_EPISODE")
        if a.extras:
            a.flags.append(f"EXTRAS:{a.extras}")
        dup = [e for e, c in collections.Counter(x[1] for x in with_ep).items() if c > 1]
        if dup:
            a.flags.append(f"DUP_EP:{len(dup)}")
        # 连续性检查
        eps = a.episodes
        gaps = [e for e in range(min(eps), max(eps) + 1) if e not in eps]
        if gaps:
            a.flags.append(f"GAPS:{len(gaps)}")
        if min(eps) > 1 and a.season == 1:
            a.flags.append(f"STARTS_AT_{min(eps)}")
        if max(eps) > 60:
            a.flags.append("EP_GT_60")
        a.confidence = "high" if not any(f.startswith(("SEASON_ASSUMED", "HALF", "STARTS_AT", "EP_GT")) for f in a.flags) else "medium"
        if not title_ok:
            a.confidence = "low"
            a.flags.append("TITLE_UNSURE")
    elif len(media) == 1 and media[0][1] is None:
        a.kind = "movie"
        # 单视频：片名优先用目录名，其次文件名
        if not title_ok or len(a.title) < 2:
            a.title, title_ok = guess_title(media[0][0].rsplit("/", 1)[-1])
            a.flags.append("TITLE_FROM_FILE")
        a.confidence = "high" if (title_ok and a.year) else ("medium" if title_ok else "low")
        if not a.year:
            a.flags.append("NO_YEAR")
        # 目录名无年份但视频名有年份
        if a.year is None:
            fy = parse_year(media[0][0])
            if fy:
                a.year = fy
                a.flags.append("YEAR_FROM_FILE")
    elif len(media) == 1:
        a.kind = "movie"
        a.flags.append("SINGLE_FILE_WITH_EP")
        a.confidence = "low"
    elif len(media) == 0:
        a.kind = "unknown"
        a.flags.append("ONLY_EXTRAS")
        a.note = f"只有花絮/特典 {a.extras} 个，无正片"
        a.confidence = "low"
    else:
        a.kind = "unknown"
        a.flags.append("MIXED_OR_NO_EP")
        a.note = f"视频 {len(media)} 个，集号识别不一致：{sorted(methods)}"
        a.confidence = "low"

    return a


def main() -> None:
    entries = load_entries()
    results = [analyze(e) for e in entries]

    by_kind = collections.Counter(r.kind for r in results)
    by_conf = collections.Counter((r.kind, r.confidence) for r in results)

    lines: list[str] = []
    lines.append("# 自动识别试探结果（真实数据）\n")
    lines.append(f"- 顶层条目：{len(results)}")
    lines.append(f"- 视频文件：{sum(r.nvid for r in results)}")
    lines.append("\n## 分类统计\n")
    lines.append("| 类型 | 条目数 |")
    lines.append("| --- | ---: |")
    for k, v in by_kind.most_common():
        lines.append(f"| {k} | {v} |")
    lines.append("\n## 置信度\n")
    lines.append("| 类型 | high | medium | low |")
    lines.append("| --- | ---: | ---: | ---: |")
    for kind in ("movie", "tv", "unknown"):
        lines.append(
            f"| {kind} | {by_conf[(kind, 'high')]} | {by_conf[(kind, 'medium')]} | {by_conf[(kind, 'low')]} |"
        )

    flag_count = collections.Counter()
    for r in results:
        for f in r.flags:
            flag_count[f.split(":")[0]] += 1
    lines.append("\n## 标记分布\n")
    lines.append("| 标记 | 条目数 |")
    lines.append("| --- | ---: |")
    for k, v in flag_count.most_common():
        lines.append(f"| {k} | {v} |")

    total_extras = sum(r.extras for r in results)
    total_videos = sum(r.nvid for r in results)
    lines.append(
        f"\n> 正片判定：{total_videos} 个视频中，{total_extras} 个被标记为花絮/特典/菜单/CM（不进正片序列）。"
    )

    # 清洗后的片名冲突（同一部片/剧出现两次）
    by_title = collections.defaultdict(list)
    for r in results:
        if r.title:
            by_title[r.title.lower()].append(r.root)
    clashes = {k: v for k, v in by_title.items() if len(v) > 1}
    lines.append("\n## 片名冲突（清洗后同名，可能是同一部剧的多个季度/版本）\n")
    if clashes:
        lines.append("| 片名 | 条目 |")
        lines.append("| --- | --- |")
        for k, v in clashes.items():
            lines.append(f"| {k} | {' / '.join('`' + x + '`' for x in v)} |")
    else:
        lines.append("无。")

    lines.append("\n## 逐条目结果\n")
    lines.append("| # | 顶层目录 | 视频 | 类型 | 可信度 | 片名猜测 | 年份 | 季 | 集 | 标记 |")
    lines.append("| ---: | --- | ---: | --- | --- | --- | ---: | ---: | --- | --- |")
    tsv_rows = ["root\tnvid\tkind\tconfidence\ttitle\tyear\tseason\tep_range\tflags"]
    for i, r in enumerate(results, 1):
        lines.append(
            f"| {i} | `{r.root}` | {r.nvid} | {r.kind} | {r.confidence} | {r.title} | "
            f"{r.year or ''} | {r.season or ''} | {r.ep_range} | {' '.join(r.flags)} |"
        )
        tsv_rows.append(
            "\t".join(
                [r.root, str(r.nvid), r.kind, r.confidence, r.title,
                 str(r.year or ""), str(r.season or ""), r.ep_range, ",".join(r.flags)]
            )
        )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_TSV.write_text("\n".join(tsv_rows) + "\n", encoding="utf-8")
    print(f"wrote {OUT_MD} and {OUT_TSV}")

    print("\n=== 分类 ===")
    for k, v in by_kind.most_common():
        print(f"  {k}: {v}")
    print("\n=== 标记 ===")
    for k, v in flag_count.most_common():
        print(f"  {k}: {v}")
    print("\n=== 需要人工确认（low / unknown）===")
    for r in results:
        if r.confidence == "low" or r.kind == "unknown":
            print(f"  [{r.kind}/{r.confidence}] {r.root}  -> {r.title}  {' '.join(r.flags)}  {r.note}")


if __name__ == "__main__":
    main()
