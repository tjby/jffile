import pathlib
import sys

rows = [
    l.split("\t", 3)
    for l in pathlib.Path("doc/inventory/upload-files.tsv").read_text(encoding="utf-8").splitlines()
    if l.startswith("f")
]

prefixes = sys.argv[1:]
for p in prefixes:
    print(f"\n## {p}")
    hits = [r[3] for r in rows if r[3].startswith(p)]
    for h in hits:
        print("  " + h)
    print(f"   ({len(hits)} files)")
