import json
import pathlib

path = sorted(pathlib.Path("reports").glob("*-preview.json"))[-1]
data = json.loads(path.read_text(encoding="utf-8"))
for seq in (44, 66, 9):
    e = next(x for x in data["entries"] if x["seq"] == seq)
    print("=" * 70)
    print(seq, e["root"][:90])
    print("kind", e["kind"], "issues", e["issues"], "media", e["media_count"], "extras", len(e["extras"]))
    for ep in e["episodes"]:
        print(f'  S{ep["season"]:02d}E{ep["episode"]:02d}{" .5" if ep["half"] else ""} {ep["method"]:7s} {ep["source"][-60:]}')
    for x in e["extras"][:5]:
        print("  extra:", x["kind"], x["source"][-70:])
