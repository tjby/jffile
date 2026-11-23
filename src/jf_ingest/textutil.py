"""文件名清洗：去发布组、去元数据、抽片名/年份/季号。

规则来自真实数据验证（doc/inventory/upload-analysis.md）：
  - 方括号分段，第一个括号基本是发布组；
  - 分辨率/编码/语言/容器/合集/来源等为「元数据段」，不参与片名；
  - 点分电影名把词拼回去（In.the.Sign.of.the.Taurus.1974）；
  - 站点水印（www.*、@xxx.com、18p2p、crazecat…）直接丢弃。
"""

from __future__ import annotations

import re

from . import media

# 单段整体等于元数据
META_TOKEN = re.compile(
    r"(?i)^(\d{1,3}(\.\d+)?|"
    r"\d{3,4}[pi]|8bit|10bit|hi10p|ma10p|"
    r"hevc|x265|x264|h\.?264|h\.?265|avc|av1|vp9|"
    r"aac|flac|ac3|eac3|dts|ddp?\d?(\.\d)?|opus|mp3|truehd|atmos|lpcm|"
    r"web-?dl|webrip|web|bdrip|bd|bluray|blu-ray|dvdrip|hdrip|hdtv|remux|tvrip|"
    r"limited|unrated|extended|proper|repack|internal|remastered|"
    r"chs|cht|gb|big5|sc|tc|jpsc|jp|jpn|eng|chi|zho|kr|kor|"
    r"简体|繁體|繁体|简繁|简繁日|简日|繁日|内封|内嵌|外挂|中字|中日|双语|"
    r"fin|tv|全集|合集|movie|raws?|raw|mkv|mp4|avi|hd\d{3,4}p|"
    r"[0-9a-f]{6,}|v\d|rev\d?)$"
)

# 出现 2 个以上就认为整段是元数据（如 "WebRip 1080p HEVC-10bit AAC"）
META_WORD = re.compile(
    r"(?i)(?<![a-z])(4k|8k|1080[pi]|720p|2160p|480p|576p|1920x1080|3840x2160|"
    r"8bit|10bit|hi10p|ma10p|hevc|x265|x264|h264|h265|avc|av1|vp9|"
    r"aac|flac|ac3|eac3|dts|ddp?|opus|mp3|truehd|atmos|lpcm|"
    r"web-?dl|webrip|web|bdrip|bd|bluray|blu-ray|dvdrip|hdrip|hdtv|remux|tvrip|"
    r"chs|cht|gb|big5|jpsc|jpn|eng|chi|zho|fin|tv|mkv|mp4|avi|srt|ass|sup)(?![a-z])"
)

CN_META = re.compile(
    r"(简体|繁體|繁体|简繁|简日|繁日|简繁日|内封|内嵌|外挂|中字|中日|双语|"
    r"国语|粤语|日语|英语|合集|全集|特典|映像|修复版|未删减|导演剪辑|"
    r"首发|中文字幕|给力剧情|三级|三級|"
    r"簡體|內封|內嵌|外掛|雙語|國語|粵語|日語|英語|修復版|未刪減|導演剪輯|"
    r"超給力|給力劇情|中文字幕|合集|全集)"
)

URL_LIKE = re.compile(
    r"(?i)www\.|\.(com|net|org|cc|me|info|tv|xyz)\b|sexinsex|18p2p|bttop|"
    r"bt\s*首发|梦幻天堂|龙网|优优|crazecat|下载加速器|18p2p|第一流氓|tto"
)

# 发布组/字幕组特征（出现在第一个括号时跳过）
GROUP_HINT = re.compile(
    r"(?i)(raw|raws|sub|subs|house|studio|team|works|ani|lilith|neko|sakurato|"
    r"lolihouse|dmg|sumisora|lk?sub|ktxp|dmhy|vcb|sao|orion|milk|prejudice|"
    r"comicat|kiss|hy|dbd|ly|h-enc|mbr|343|cheese|shigure|lpsub|lilith)"
)

SEASON_IN_NAME = re.compile(
    r"(?i)(?<![a-z])(?:s(\d{1,2})(?![\deex]))"
    r"|season\s*(\d{1,2})"
    r"|第\s*([一二三四五六七八九十\d]{1,3})\s*季"
)
ORDINAL_SEASON = re.compile(r"(?i)(\d{1,2})(?:st|nd|rd|th)\s*season")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?![\dpxX×])")

_ALL_EXT = (
    media.VIDEO_EXT | media.SUB_EXT | media.VOBSUB_EXT | media.SUB_EXT_UNSUPPORTED
    | media.IMAGE_EXT | media.AUDIO_EXT | media.ARCHIVE_EXT | media.JUNK_EXT
)


# ---------------------------------------------------------------- 基础工具


def strip_known_ext(name: str) -> str:
    """只去掉「已知的」扩展名；`美姐妹肉奴隶.Bishimai` 不会被误切。"""
    if "." not in name:
        return name
    head, tail = name.rsplit(".", 1)
    if ("." + tail.lower()) in _ALL_EXT or tail.lower() in {"mkv", "mp4", "avi"}:
        return head
    return name


def tokens(name: str) -> list[tuple[str, str, int]]:
    """按顺序返回 (类型, 词, 文本段号)。

    类型：`br` = 括号段（整体一词），`text` = 括号外的文本（按 . _ 拆词）。
    文本段号用于把同一段里的词重新拼回片名。
    """
    out: list[tuple[str, str, int]] = []
    run = 0
    pos = 0
    for m in re.finditer(r"\[([^\]]*)\]|\(([^)]*)\)|【([^】]*)】", name):
        pre = name[pos:m.start()]
        if pre.strip():
            for piece in re.split(r"[._]+", pre):
                piece = piece.strip()
                if piece:
                    out.append(("text", piece, run))
            run += 1
        val = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if val:
            out.append(("br", val, -1 - len(out)))
        pos = m.end()
    tail = name[pos:]
    if tail.strip():
        for piece in re.split(r"[._]+", tail):
            piece = piece.strip()
            if piece:
                out.append(("text", piece, run))
    return out


NATIONALITY = re.compile(
    r"^(日本|日活|欧美|歐美|美国|美國|韩国|韓國|泰国|泰國|菲律宾|菲律賓|"
    r"香港|台湾|台灣|中国|中國|意大利|意大利|法国|法國|西班牙|捷克|俄罗斯|俄羅斯)$"
)


def is_meta(seg: str) -> bool:
    s = seg.strip()
    if not s:
        return True
    if NATIONALITY.match(s):
        return True
    if META_TOKEN.match(s.replace(" ", "")):
        return True
    if re.fullmatch(r"[\d.\-–~ ]+", s):
        return True
    if re.fullmatch(r"(?i)\d{1,3}(-\d{1,3})?\s*(fin|tv|全集)?", s):
        return True
    if URL_LIKE.search(s):
        return True
    # 以分辨率/介质开头（1080P版HD-MP4-5.43G、DVD-MP4-1G）
    if META_WORD.match(s):
        return True
    if re.fullmatch(r"\d{6,8}", s):  # 20240108 这类日期
        return True
    if len(META_WORD.findall(s)) >= 2 and not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", s):
        return True
    if CN_META.search(s):
        # 只有「整段几乎都是标签」才算元数据；
        # `威而钢之雄霸天下.未删减` 这种要留下来当片名
        rest = re.sub(r"[\s._\-\[\]()（）·:：]", "", CN_META.sub("", s))
        if len(rest) <= 2:
            return True
    return False


def _clean_piece(s: str) -> str:
    s = s.strip(" ._-,;:·|")
    s = re.sub(r"^\[[^\]]*\]", "", s).strip()
    return s


def _strip_tail_tags(title: str) -> str:
    t = title.strip()
    for _ in range(4):
        before = t
        t = re.sub(r"(?i)\s*[-–]?\s*(19|20)\d{2}\s*$", "", t)
        t = re.sub(
            r"(?i)[\s.]+(中文字幕|中字|双语|国语|粤语|日语|英语|官方[^\s]*中字?|"
            r"未删减|未刪減|修复版|修復版|加长版|加長版|导演剪辑版|"
            r"hd\d{3,4}p|\d{3,4}p|bluray|bdrip|webrip|web-dl|remux)\s*$",
            "", t,
        )
        t = t.strip(" ._-,;:·|")
        if t == before:
            break
    return t


def strip_season_marker(title: str) -> str:
    """去掉片名尾部的 S02 / 2nd Season / 第三季（季号单独存在）。"""
    t = title.strip()
    t = re.sub(r"(?i)\s*[-–]?\s*(?:s|season)\s*\d{1,2}\s*$", "", t)
    t = re.sub(r"(?i)\s*[-–]?\s*\d{1,2}(?:st|nd|rd|th)\s+season\s*$", "", t)
    t = re.sub(r"\s*第\s*[一二三四五六七八九十\d]{1,3}\s*季\s*$", "", t)
    return t.strip(" ._-,;:·|")


def _looks_taggy(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if META_WORD.match(t) or META_TOKEN.match(t.replace(" ", "")):
        return True
    if re.match(r"(?i)^(hd|dvd|bd|mp4|mkv|avi|web)[-_. ]?\d", t):
        return True
    if CN_META.search(t):
        rest = re.sub(r"[\s._\-\[\]()（）·:：]", "", CN_META.sub("", t))
        return len(rest) <= 2
    return False


def _split_site_prefix(name: str) -> str:
    """`道@sexinsex.net@美女海滩` → `美女海滩`；`...@MKVChina 9.51GB` → 保留标题部分。"""
    if "@" not in name:
        return name
    parts = [p.strip() for p in name.split("@") if p.strip()]
    if len(parts) < 2:
        return name
    kept = [
        p for p in parts
        if not (URL_LIKE.search(p) and len(p) < 30 and not re.search(r"[\u4e00-\u9fff]", p))
    ]
    kept = [
        p for p in kept
        if not (len(p) <= 4 and not re.search(r"[\u4e00-\u9fff]", p))
    ]
    return max(kept or parts, key=len)


# ---------------------------------------------------------------- 解析入口


def guess_title(name: str) -> tuple[str, bool]:
    """返回 (片名, 是否可信)。name 可以是目录名或文件名（含扩展名）。"""
    name = _split_site_prefix(strip_known_ext(name))
    name = re.sub(r"(?i)(18p2p|sexinsex|第一流氓|tto|bttop|bt\s*首发)", "", name)
    tk = tokens(name)
    if not tk:
        return "", False

    starts_with_bracket = name.lstrip().startswith("[")
    first_bracket = tk[0][1] if tk and tk[0][0] == "br" else ""
    # 第一个括号是发布组的条件：命中组名特征、纯 ASCII、或国家/地区标签
    skip_first = bool(first_bracket) and (
        bool(GROUP_HINT.search(first_bracket))
        or not re.search(r"[\u4e00-\u9fff]", first_bracket)
        or bool(NATIONALITY.match(first_bracket.strip()))
    )

    def collect(skip_first_bracket: bool) -> list[tuple[int, str, str, int]]:
        found: list[tuple[int, str, str, int]] = []
        skipped = False
        for idx, (kind, val, run) in enumerate(tk):
            if is_meta(val):
                continue
            cleaned = _clean_piece(val)
            if not cleaned:
                continue
            if (
                skip_first_bracket
                and skip_first
                and kind == "br"
                and idx == 0
                and starts_with_bracket
                and not skipped
                and len(tk) > 1
            ):
                skipped = True
                continue
            found.append((idx, kind, cleaned, run))
        return found

    cands = collect(True)
    if not cands:
        # 整个名字只有「第一个括号 + 一堆元数据」，那第一个括号就是片名
        cands = collect(False)
    else:
        # 第一个候选看起来仍是标签（1080P版HD-MP4…、美國超給力劇情），
        # 说明真片名被第一个括号挡掉了，回退到包含第一个括号的解析
        first = cands[0][2]
        if _looks_taggy(first):
            alt = collect(False)
            if alt and not _looks_taggy(alt[0][2]):
                cands = alt

    if not cands:
        return _clean_piece(tk[0][1]), False

    pos, kind, val, run = cands[0]
    title = val

    if kind == "text":
        parts = [val]
        for _k2, v2, r2 in tk[pos + 1:]:
            if r2 != run:
                break
            if re.fullmatch(r"(19|20)\d{2}", v2) or is_meta(v2):
                break
            parts.append(v2)
        title = " ".join(parts)

    title = strip_season_marker(_strip_tail_tags(title))
    sure = True
    if len(title) < 3 and not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", title):
        sure = len(cands) == 1 and len(title) >= 2
    if URL_LIKE.search(title) or GROUP_HINT.fullmatch(title.strip("[]")):
        sure = False
    return title, sure


def parse_year(text: str) -> int | None:
    years = [int(y) for y in _YEAR.findall(text)]
    years = [y for y in years if 1900 <= y <= 2100]
    return years[-1] if years else None


def parse_season(text: str) -> int | None:
    m = SEASON_IN_NAME.search(text)
    if m:
        for g in m.groups():
            if not g:
                continue
            if g.isdigit():
                return int(g)
            return CN_NUM.get(g)
    m = ORDINAL_SEASON.search(text)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------- 目标名清洗

_RESERVED = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str, replacement: str = "-") -> str:
    """替换 Jellyfin/文件系统保留字符，去掉首尾空白和点。"""
    s = _RESERVED.sub(replacement, name)
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" .")


def truncate_utf8(name: str, limit: int = 240) -> str:
    """按 UTF-8 字节截断，保留扩展名（调用方先分离扩展名会更稳）。"""
    data = name.encode("utf-8")
    if len(data) <= limit:
        return name
    cut = data[:limit].decode("utf-8", "ignore")
    return cut.rstrip(" ._-")
