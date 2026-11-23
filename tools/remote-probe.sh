#!/bin/sh
# 只读环境探测
echo "=== tools ==="
for c in tree 7z 7za unzip ffprobe mediainfo python3 pip3; do
  p=$(command -v "$c" 2>/dev/null)
  if [ -n "$p" ]; then echo "$c: $p"; else echo "$c: missing"; fi
done
echo
echo "=== jellyfin data ==="
ls /var/lib/jellyfin 2>&1 | head -20
echo "--- default libs ---"
ls /var/lib/jellyfin/root/default 2>&1 | head -20
echo "--- library options ---"
cat /var/lib/jellyfin/root/default/*/options.xml 2>/dev/null | head -40
echo
echo "=== acl uploads ==="
getfacl -p /data/jellyfin/uploads 2>&1 | head -12
echo
echo "=== inodes / fs ==="
df -i /data | tail -2
echo
echo "=== sample encodings ==="
ls /data/jellyfin/uploads | head -3 | od -c | head -6
