import collections
import pathlib

rows = [
    l.split("\t")
    for l in pathlib.Path("doc/inventory/parse-probe.tsv").read_text(encoding="utf-8").splitlines()[1:]
]
agg = collections.Counter()
vids = collections.Counter()
for r in rows:
    agg[r[2]] += 1
    vids[r[2]] += int(r[1])
print("条目:", dict(agg))
print("视频:", dict(vids))
print("合计条目:", sum(agg.values()), "合计视频:", sum(vids.values()))
tv = [r for r in rows if r[2] == "tv"]
print("剧集条目平均视频数:", round(sum(int(r[1]) for r in tv) / len(tv), 1))
print("单视频剧集条目:", [r[0][:50] for r in tv if int(r[1]) == 1])
