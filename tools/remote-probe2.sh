#!/bin/sh
echo "=== python env ==="
python3 -V
python3 -m pip --version 2>&1 | head -2
python3 -m venv --help >/dev/null 2>&1 && echo "venv: ok" || echo "venv: missing"
python3 -c "import tomllib" 2>&1 | tail -1
python3 -c "import sqlite3,zipfile,urllib.request,json,shutil,fcntl; print('stdlib ok')"
echo
echo "=== tv library path ==="
for f in /var/lib/jellyfin/root/default/*/options.xml; do
  echo "-- $f"
  grep -A1 "<Path>" "$f" | head -4
done
echo
echo "=== config dirs ==="
ls -ld /etc/jellyfin /var/lib/jellyfin /var/cache/jellyfin 2>&1
echo
echo "=== uploads sample sizes ==="
du -sh /data/jellyfin/uploads/* 2>/dev/null | sort -h | tail -5
