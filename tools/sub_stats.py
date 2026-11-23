"""字幕与附件的数据面统计。"""

import collections
import pathlib
import re

VIDEO = {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".webm", ".iso", ".mpg", ".mpeg", ".m4v"}
SUB = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".smi", ".ttml", ".dfxp", ".sup"}

rows = []
for line in pathlib.Path("doc/inventory/upload-files.tsv").read_text(encoding="utf-8").splitlines():
    if not line.startswith("f"):
        continue
    typ, size, mtime, rel = line.split("\t", 3)
    rows.append((rel, int(size)))

by_dir = collections.defaultdict(list)
for rel, size in rows:
    by_dir[rel.rsplit("/", 1)[0] if "/" in rel else ""].append(rel)

ext = lambda p: pathlib.PurePosixPath(p).suffix.lower()

subs = [r for r, _ in rows if ext(r) in SUB]
videos = [r for r, _ in rows if ext(r) in VIDEO]

print(f"字幕文件 {len(subs)}，视频 {len(videos)}")

# 1. 字幕所在目录有没有视频
no_video_dir = [s for s in subs if not any(ext(v) in VIDEO for v in by_dir[s.rsplit("/", 1)[0] if "/" in s else ""])]
print(f"\n1) 所在目录没有视频的字幕: {len(no_video_dir)}")
for s in no_video_dir[:20]:
    print("   " + s)

# 2. 前缀配对
paired = collections.Counter()
orphan = []
for s in subs:
    d = s.rsplit("/", 1)[0] if "/" in s else ""
    stem = pathlib.PurePosixPath(s).stem.lower()
    hit = False
    for v in by_dir.get(d, []):
        if ext(v) not in VIDEO:
            continue
        vs = pathlib.PurePosixPath(v).stem.lower()
        if stem == vs or (stem.startswith(vs) and stem[len(vs):][:1] in ".-_ "):
            hit = True
            break
    if hit:
        paired[d] += 1
    else:
        orphan.append(s)
print(f"\n2) 同目录前缀匹配成功的字幕: {len(subs) - len(orphan)}，孤儿: {len(orphan)}")
for s in orphan[:20]:
    print("   " + s)

# 3. 每个视频配几份字幕
per_video = collections.Counter()
for v in videos:
    d = v.rsplit("/", 1)[0] if "/" in v else ""
    vs = pathlib.PurePosixPath(v).stem.lower()
    n = 0
    for s in by_dir.get(d, []):
        if ext(s) not in SUB:
            continue
        ss = pathlib.PurePosixPath(s).stem.lower()
        if ss == vs or (ss.startswith(vs) and ss[len(vs):][:1] in ".-_ "):
            n += 1
    if n:
        per_video[n] += 1
print("\n3) 每个视频的字幕份数分布:")
for k in sorted(per_video):
    print(f"   {k} 份字幕: {per_video[k]} 个视频")
print(f"   合计有外挂字幕的视频: {sum(per_video.values())}")

# 4. 字幕标签分布
tags = collections.Counter()
for s in subs:
    stem = pathlib.PurePosixPath(s).stem
    parts = re.split(r"[._\- ]+", stem)
    for p in parts:
        if p.lower() in {"sc", "tc", "chs", "cht", "gb", "big5", "zh", "cn", "chi", "zho", "jpn", "jp", "eng", "en", "jpsc", "default", "forced", "sdh"}:
            tags[p.lower()] += 1
print("\n4) 字幕名里出现的语言/标志段:")
for k, v in tags.most_common():
    print(f"   {k}: {v}")

# 5. 压缩包里的字幕
arch = [r for r, _ in rows if ext(r) in {".7z", ".zip", ".rar"}]
sub_arch = [a for a in arch if re.search(r"(?i)sub|字幕|sup|font", a)]
print(f"\n5) 压缩包 {len(arch)} 个，其中疑似含字幕/字体: {len(sub_arch)}")
for a in sub_arch:
    print("   " + a)

# 6. 图片 / 音频 / 种子 分布
for name, exts in (("图片", {".jpg", ".jpeg", ".png", ".avif", ".webp", ".gif"}), ("音频", {".flac", ".mp3", ".m4a", ".aac", ".wav", ".opus"})):
    files = [r for r, _ in rows if ext(r) in exts]
    print(f"\n6) {name} {len(files)} 个")
    roots = collections.Counter(f.split("/", 1)[0] for f in files)
    for k, v in roots.most_common(5):
        print(f"   {v:4d}  {k}")
