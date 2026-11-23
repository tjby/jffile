#!/bin/sh
echo "=== reachability ==="
for u in https://api.bgm.tv/v0/subjects/1 https://bgm.tv https://movie.douban.com https://api.douban.com/v2/ https://raw.githubusercontent.com; do
  code=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$u" 2>/dev/null)
  echo "$u -> ${code:-fail}"
done
echo
echo "=== venv/pip ==="
rm -rf /tmp/tv && python3 -m venv /tmp/tv 2>&1 | head -3
if [ -x /tmp/tv/bin/pip ]; then echo "pip in venv: yes"; /tmp/tv/bin/pip -V; else echo "pip in venv: no"; fi
rm -rf /tmp/tv
