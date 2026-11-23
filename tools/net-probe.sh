#!/bin/sh
for u in https://api.themoviedb.org/3/ https://api4.thetvdb.com/v4/ https://www.google.com https://pypi.org/simple/; do
  code=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$u" 2>/dev/null)
  echo "$u -> ${code:-fail}"
done
