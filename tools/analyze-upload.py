"""分析 doc/inventory/upload-files.tsv，输出统计与样本，辅助制定识别规则。

用法: python tools/analyze-upload.py [tsv路径]
"""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

TSV = Path(sys.argv[1] if len(sys.argv) > 1 else "doc/inventory/upload-files.tsv")

VIDEO_EXT = {
    ".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv",
    ".webm", ".iso", ".mpg", ".mpeg", ".rmvb", ".rm", ".vob", ".m4v",
}
SUB_EXT = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".smi", ".ttml", ".dfxp", ".sup"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".avif", ".webp", ".bmp", ".gif"}
AUDIO_EXT = {".flac", ".mp3", ".m4a", ".aac", ".wav", ".ape", ".ogg", ".opus"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".xz"}
JUNK_EXT = {".torrent", ".xltd", ".exe", ".docx", ".log", ".txt", ".cue", ".part", ".!qb", ".tmp"}

ROW = re.compile(r"^(?P<type>[a-z])\t(?P<size>\d+)\t(?P<mtime>[^\t]+)\t(?P<path>.*)$")
SXXEXX = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})e(\d{1,3})")
CN_EP = re.compile(r"(?i)(第\s*\d{1,4}\s*[集话]|\d{1,3}\s*[集话]|ep?\s*\d{1,4}|\[\d{1,3}(?:\.5|v2)?\])")
BRACKET_NUM = re.compile(r"\[(\d{1,3})(?:\.5|v\d)?\]")
DASH_NUM = re.compile(r"(?:^|[\s._-])(\d{1,3})(?:\.5|v\d)?(?:$|[\s._\[])")


def load():
    rows = []
    for line in TSV.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m:
            rows.append(
                {
                    "type": m["type"],
                    "size": int(m["size"]),
                    "path": m["path"],
                    "name": m["path"].rsplit("/", 1)[-1],
                    "dir": m["path"].rsplit("/", 1)[0] if "/" in m["path"] else "",
                    "root": m["path"].split("/", 1)[0],
                }
            )
    return rows


def ext(name: str) -> str:
    return Path(name).suffix.lower()


def stem(name: str) -> str:
    return Path(name).stem


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main():
    rows = load()
    files = [r for r in rows if r["type"] == "f"]
    videos = [r for r in files if ext(r["name"]) in VIDEO_EXT]
    subs = [r for r in files if ext(r["name"]) in SUB_EXT]

    section("0. 规模")
    print(f"条目 {len(rows)}，文件 {len(files)}，视频 {len(videos)}，字幕 {len(subs)}")
    print(f"视频总大小 {human(sum(r['size'] for r in videos))}")

    section("1. 顶层：目录 vs 散文件")
    root_kind = collections.Counter()
    loose = []
    for r in rows:
        if "/" not in r["path"]:
            root_kind[r["type"]] += 1
            if r["type"] == "f":
                loose.append(r)
    print(f"顶层目录 {root_kind['d']} 个，顶层散文件 {root_kind['f']} 个")
    for r in loose:
        print(f"  [散文件] {ext(r['name']):8s} {human(r['size']):>9s}  {r['path']}")

    by_root = collections.defaultdict(list)
    for r in files:
        by_root[r["root"]].append(r)

    section("2. 顶层条目：视频数分布（判断「电影 vs 剧集」）")
    dist = collections.Counter()
    for root, items in by_root.items():
        n = sum(1 for i in items if ext(i["name"]) in VIDEO_EXT)
        dist[n] += 1
    for k in sorted(dist):
        print(f"  视频数 {k:>3d}: {dist[k]:>3d} 个顶层条目")

    section("3. 每个顶层条目的构成（视频>=1 的前 40 个，按视频数降序）")
    info = []
    for root, items in by_root.items():
        vids = [i for i in items if ext(i["name"]) in VIDEO_EXT]
        if not vids:
            continue
        info.append((len(vids), root, vids, items))
    info.sort(key=lambda x: -x[0])
    for n, root, vids, items in info[:40]:
        esub = sum(1 for i in items if ext(i["name"]) in SUB_EXT)
        eimg = sum(1 for i in items if ext(i["name"]) in IMG_EXT)
        eaud = sum(1 for i in items if ext(i["name"]) in AUDIO_EXT)
        print(f"  {n:>3d} 视频 / {esub:>2d} 字幕 / {eimg:>3d} 图 / {eaud:>2d} 音 | {root}")

    section("4. 视频文件名里的「集号」形态")
    pat = collections.Counter()
    samples = collections.defaultdict(list)
    for r in videos:
        s = stem(r["name"])
        if SXXEXX.search(s):
            k = "SxxExx"
        elif re.search(r"[第]\s*\d{1,3}\s*[集话]", s):
            k = "第N集/话"
        elif BRACKET_NUM.search(s):
            k = "[NN] 方括号"
        elif re.search(r"(?i)(^|[\s._-])(ep?|e)\s?\d{1,3}([\s._-]|$)", s):
            k = "EPxx / Exx"
        elif DASH_NUM.search(s):
            k = " - NN / .NN."
        elif re.fullmatch(r"\d{1,3}(\.5|v\d)?", s):
            k = "纯数字文件名"
        else:
            k = "无法识别集号"
        pat[k] += 1
        if len(samples[k]) < 6:
            samples[k].append(r["path"])
    for k, v in pat.most_common():
        print(f"  {k:>12s}: {v:>4d}")
        for p in samples[k]:
            print(f"      · {p}")

    section("5. 顶层名里的「季」标记")
    season_root = [r for r in rows if r["type"] == "d" and "/" not in r["path"]
                   and re.search(r"(?i)(season\s*\d+|第[一二三四五六七八九十\d]+季|\d(st|nd|rd|th)\s*season|\bS\d{1,2}\b)", r["path"])]
    print(f"命中 {len(season_root)} 个：")
    for r in season_root:
        print(f"  · {r['path']}")
    print("显式 Season 目录：", sum(1 for r in rows if r["type"] == "d" and re.search(r"(?i)(^|/)season[ ._-]*\d+", r["path"])))

    section("6. 字幕配对（按 Jellyfin 规则：视频主名为前缀 + 分隔符）")
    dir_subs = collections.defaultdict(list)
    for s in subs:
        dir_subs[s["dir"]].append(s)
    paired = collections.Counter()
    unpaired = []
    for v in videos:
        st = stem(v["name"]).lower()
        found = []
        for s in dir_subs.get(v["dir"], []):
            ss = stem(s["name"]).lower()
            if ss == st or (ss.startswith(st) and ss[len(st):][:1] in ".-_ "):
                found.append(s)
        if found:
            paired[len(found)] += 1
        else:
            unpaired.append(v)
    print(f"有字幕的视频 {sum(paired.values())}，其中多份字幕的视频 {sum(c for k, c in paired.items() if k > 1)}")
    print(f"无字幕视频 {len(unpaired)}")
    used = set()
    for v in videos:
        st = stem(v["name"]).lower()
        for s in dir_subs.get(v["dir"], []):
            ss = stem(s["name"]).lower()
            if ss == st or (ss.startswith(st) and ss[len(st):][:1] in ".-_ "):
                used.add(s["path"])
    orphans = [s for s in subs if s["path"] not in used]
    print(f"孤儿字幕 {len(orphans)}，示例：")
    for s in orphans[:15]:
        print(f"  · {s['path']}")

    section("7. 附件/花絮/图片目录命名")
    extra_dirs = collections.Counter()
    for r in rows:
        if r["type"] != "d":
            continue
        for part in r["path"].split("/"):
            if re.fullmatch(r"(?i)(extras?|scans?|specials?|sp|sps|cds?|bonus|menus?|ncop|nced|pv|cm|trailers?|samples?|ost|soundtracks?|subs?|subtitles?|fonts?|manga|music|artwork|cover)", part):
                extra_dirs[part.lower()] += 1
    for k, v in extra_dirs.most_common():
        print(f"  {k:>14s}: {v}")

    section("8. 非媒体文件构成")
    buckets = collections.Counter()
    bucket_size = collections.Counter()
    for r in files:
        e = ext(r["name"])
        if e in VIDEO_EXT or e in SUB_EXT:
            continue
        if e in IMG_EXT:
            k = "图片"
        elif e in AUDIO_EXT:
            k = "音频"
        elif e in ARCHIVE_EXT:
            k = "压缩包"
        elif e in JUNK_EXT:
            k = "垃圾/种子/临时"
        elif e in {".nfo", ".xml", ".json"}:
            k = "元数据"
        else:
            k = "其它"
        buckets[k] += 1
        bucket_size[k] += r["size"]
    for k, v in buckets.most_common():
        print(f"  {k:>12s}: {v:>4d} 个, {human(bucket_size[k])}")

    section("9. 可疑文件（临时/未完成/0 字节）")
    susp = [r for r in files if ext(r["name"]) in {".part", ".xltd", ".!qb", ".tmp", ".downloading"} or r["size"] == 0]
    print(f"命中 {len(susp)}")
    for r in susp[:20]:
        print(f"  · {human(r['size']):>9s}  {r['path']}")

    section("10. 重名视频（跨目录同名文件，可能是重复/多版本）")
    by_name = collections.defaultdict(list)
    for v in videos:
        by_name[v["name"].lower()].append(v["path"])
    dups = {k: v for k, v in by_name.items() if len(v) > 1}
    print(f"同名视频组 {len(dups)}")
    for k, v in list(dups.items())[:15]:
        print(f"  · {k}")
        for p in v:
            print(f"      {p}")

    section("11. 电影类条目：内层视频名 vs 顶层目录名是否一致")
    mism = 0
    samples_mism = []
    for n, root, vids, items in info:
        if n != 1 or "/" in root:
            continue
        s = stem(vids[0]["name"])
        if s.lower() != root.lower():
            mism += 1
            if len(samples_mism) < 12:
                samples_mism.append((root, s))
    print(f"单视频条目 {sum(1 for x in info if x[0] == 1)}，其中视频名≠目录名 {mism}")
    for root, s in samples_mism:
        print(f"  目录: {root}\n  视频: {s}\n")

    section("12. 路径深度分布")
    depth = collections.Counter(len(r["path"].split("/")) for r in rows)
    for k in sorted(depth):
        print(f"  深度 {k}: {depth[k]}")

    section("13. 无视频的顶层条目（可能只是字幕/图/压缩包）")
    for root, items in by_root.items():
        if not any(ext(i["name"]) in VIDEO_EXT for i in items):
            print(f"  · {root}  ({len(items)} 个文件, {human(sum(i['size'] for i in items))})")


if __name__ == "__main__":
    main()
