import json
import pathlib

path = sorted(pathlib.Path("reports").glob("*-preview.json"))[-1]
data = json.loads(path.read_text(encoding="utf-8"))
print("报告:", path)
for e in data["entries"]:
    if e["kind"] != "movie":
        continue
    print(
        f'{e["seq"]:3d} {e["confidence"]:6s} {str(e["year"] or ""):5s} '
        f'{e["title"][:46]:48s} <= {e["root"][:50]}'
    )
