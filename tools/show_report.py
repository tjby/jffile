import json
import pathlib
import sys

path = sorted(pathlib.Path("reports").glob("*-preview.json"))[-1]
data = json.loads(path.read_text(encoding="utf-8"))
print("报告:", path)
for e in data["entries"]:
    if e["confidence"] != "high":
        print(f'{e["confidence"]:6s} {e["kind"]:6s} seq={e["seq"]:<3d} {e["title"][:40]:42s} {",".join(e["issues"])}  <= {e["root"][:55]}')
