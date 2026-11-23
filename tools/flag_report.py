"""统计各类标记对应的条目，用于写分析文档。"""

import pathlib

rows = [
    l.split("\t")
    for l in pathlib.Path("doc/inventory/parse-probe.tsv").read_text(encoding="utf-8").splitlines()[1:]
]

groups = {}
for r in rows:
    for f in r[8].split(","):
        key = f.split(":")[0]
        if key:
            groups.setdefault(key, []).append((r[0], r[4], r[6], r[7], f))

for key in ("DUP_EP", "GAPS", "HALF_EPISODE", "EP_GT_60", "EXTRAS", "STARTS_AT_2", "STARTS_AT_3", "STARTS_AT_4", "STARTS_AT_5", "STARTS_AT_6", "STARTS_AT_9"):
    print(f"\n## {key} ({len(groups.get(key, []))})")
    for root, title, season, eps, flag in groups.get(key, []):
        print(f"  S{season or '?'} E{eps:<8s} {title[:45]:47s} {flag:<12s} {root[:60]}")
