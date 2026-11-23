import collections
import json
import pathlib

path = sorted(pathlib.Path("doc/inventory/real-run").glob("*-preview.json"))[-1]
data = json.loads(path.read_text(encoding="utf-8"))
by = collections.Counter((e["kind"], e["confidence"]) for e in data["entries"])
kinds = collections.Counter(e["kind"] for e in data["entries"])
vids = collections.Counter()
for e in data["entries"]:
    vids[e["kind"]] += e["media_count"]
print("文件:", path.name)
print("条目:", dict(kinds))
print("视频:", dict(vids))
for k in ("movie", "tv", "unknown"):
    print(k, {c: by[(k, c)] for c in ("high", "medium", "low")})
print("总计:", dict(collections.Counter(e["confidence"] for e in data["entries"])))
print("花絮:", sum(len(e["extras"]) for e in data["entries"]))
