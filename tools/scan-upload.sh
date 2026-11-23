#!/usr/bin/env bash
# 只读清点 /data/jellyfin/uploads 的结构，生成 Markdown 报告 + TSV 完整清单。
# 用法: bash scan-upload.sh [源目录] [输出md] [输出tsv]
set -u
export LC_ALL=C.UTF-8 2>/dev/null || export LC_ALL=en_US.UTF-8

SRC="${1:-/data/jellyfin/uploads}"
OUT_MD="${2:-/tmp/jf_inventory.md}"
OUT_TSV="${3:-/tmp/jf_files.tsv}"

command -v find >/dev/null || { echo "缺少 find" >&2; exit 1; }

# 完整清单: 类型 / 字节数 / mtime / 相对路径
find "$SRC" -mindepth 1 -printf '%y\t%s\t%TY-%Tm-%Td %TH:%TM\t%P\n' 2>/dev/null |
  sort -t $'\t' -k4,4 > "$OUT_TSV"

TOTAL_FILES=$(awk -F'\t' '$1=="f"' "$OUT_TSV" | wc -l)
TOTAL_DIRS=$(awk -F'\t' '$1=="d"' "$OUT_TSV" | wc -l)
TOTAL_LINKS=$(awk -F'\t' '$1=="l"' "$OUT_TSV" | wc -l)
TOTAL_BYTES=$(awk -F'\t' '$1=="f"{s+=$2} END{print s+0}' "$OUT_TSV")

human() { awk -v n="$1" 'BEGIN{split("B KB MB GB TB",u," ");i=1;while(n>=1024&&i<5){n/=1024;i++}printf "%.1f %s",n,u[i]}'; }

{
  echo "# /data/jellyfin/uploads 目录清点"
  echo
  echo "| 项目 | 值 |"
  echo "| --- | --- |"
  echo "| 生成时间 | $(date '+%Y-%m-%d %H:%M:%S %Z') |"
  echo "| 主机 | $(hostname) |"
  echo "| 扫描路径 | \`$SRC\` |"
  echo "| 文件数 | $TOTAL_FILES |"
  echo "| 目录数 | $TOTAL_DIRS |"
  echo "| 符号链接数 | $TOTAL_LINKS |"
  echo "| 文件总大小 | $(human "$TOTAL_BYTES") ($TOTAL_BYTES 字节) |"
  echo "| 磁盘占用 (du -sh) | $(du -sh "$SRC" 2>/dev/null | cut -f1) |"
  echo
  echo "> 完整逐文件清单（类型/大小/修改时间/相对路径）见同目录的 \`$(basename "$OUT_TSV")\`。"
  echo
  echo "## 1. 扩展名分布"
  echo
  echo "| 扩展名 | 数量 | 总大小 |"
  echo "| --- | ---: | ---: |"
  awk -F'\t' '
    $1=="f" {
      name=$4; sub(/.*\//,"",name);
      if (match(name, /\.[^.]+$/)) { ext=tolower(substr(name, RSTART)) } else { ext="(无扩展名)" }
      cnt[ext]++; sz[ext]+=$2
    }
    function human(n,  u,i){split("B KB MB GB TB",u," ");i=1;while(n>=1024&&i<5){n/=1024;i++}return sprintf("%.1f %s",n,u[i])}
    END { for (e in cnt) printf "| `%s` | %d | %s |\n", e, cnt[e], human(sz[e]) }
  ' "$OUT_TSV" | sort -t'|' -k3,3nr | head -40
  echo
  echo "## 2. 顶层条目（每个条目 = 一部电影 / 一部剧集 / 散文件）"
  echo
  echo "| 顶层名称 | 类型 | 文件数 | 子目录数 | 最大层级 | 总大小 |"
  echo "| --- | --- | ---: | ---: | ---: | ---: |"
  awk -F'\t' '
    {
      split($4, p, "/"); root=p[1];
      if ($1=="f") { files[root]++; sz[root]+=$2 }
      else if ($1=="d") { dirs[root]++; if (index($4,"/")>0 || 1) { d=split($4,q,"/"); if (d>depth[root]) depth[root]=d } }
      else { other[root]++ }
      seen[root]=1
    }
    function human(n,  u,i){split("B KB MB GB TB",u," ");i=1;while(n>=1024&&i<5){n/=1024;i++}return sprintf("%.1f %s",n,u[i])}
    END {
      for (r in seen) printf "%s\t%d\t%d\t%d\t%s\n", r, files[r]+0, dirs[r]+0, depth[r]+0, human(sz[r]+0)
    }
  ' "$OUT_TSV" | sort -t $'\t' -k5,5hr | while IFS=$'\t' read -r name nf nd dep sz; do
      if [ -d "$SRC/$name" ]; then t="目录"; else t="文件"; fi
      printf '| `%s` | %s | %s | %s | %s | %s |\n' "$name" "$t" "$nf" "$nd" "$dep" "$sz"
    done
  echo
  echo "## 3. 命名模式统计"
  echo
  echo "| 模式 | 命中文件数 |"
  echo "| --- | ---: |"
  echo "| 含 \`SxxExx\`（标准季集） | $(awk -F'\t' '$1=="f"{print $4}' "$OUT_TSV" | grep -cE '(^|[^0-9A-Za-z])[Ss][0-9]{1,2}[Ee][0-9]{1,3}' || true) |"
  echo "| 含 \`第N集\` / \`EPxx\` / 单独 \`Exx\` | $(awk -F'\t' '$1=="f"{print $4}' "$OUT_TSV" | grep -cE '第[0-9一二三四五六七八九十百]+[集话]|(^|[^A-Za-z])[Ee][Pp]?[0-9]{1,3}([^0-9]|$)' || true) |"
  echo "| 含 \`Season xx\` 目录名 | $(awk -F'\t' '$1=="d"{print $4}' "$OUT_TSV" | grep -cE '(^|/)Season[ ._-]*[0-9]{1,2}(/|$)' || true) |"
  echo "| 含中文文件/目录名 | $(awk -F'\t' '$1!="d"||1{print $4}' "$OUT_TSV" | grep -cP '[\x{4e00}-\x{9fff}]' || true) |"
  echo "| 名称含空格 | $(awk -F'\t' '{print $4}' "$OUT_TSV" | grep -c ' ' || true) |"
  echo "| 名称含 Windows 非法字符 \`:*?\"<>|\` | $(awk -F'\t' '{print $4}' "$OUT_TSV" | grep -cE '[:*?"<>|]' || true) |"
  echo "| 名称超过 200 字节 | $(awk -F'\t' '{print $4}' "$OUT_TSV" | awk 'length($0)>200' | wc -l) |"
  echo
  echo "## 4. 目录树（深度 3，最多 400 行）"
  echo
  echo '```text'
  if command -v tree >/dev/null 2>&1; then
    tree -L 3 --charset utf-8 -h --dirsfirst "$SRC" 2>/dev/null | head -400
  else
    find "$SRC" -maxdepth 3 -printf '%y %p\n' 2>/dev/null | sed "s|$SRC|.|" | sort -k2 | head -400
  fi
  echo '```'
  echo
  echo "## 5. 视频文件样本（前 20 个，按路径排序）"
  echo
  echo '```text'
  awk -F'\t' '$1=="f"{n=$4; e=tolower(n); if (e ~ /\.(mkv|mp4|avi|ts|m2ts|mov|wmv|flv|webm|iso)$/) print $4}' "$OUT_TSV" | head -20
  echo '```'
  echo
  echo "## 6. 字幕文件样本（前 20 个）"
  echo
  echo '```text'
  awk -F'\t' '$1=="f"{e=tolower($4); if (e ~ /\.(srt|ass|ssa|vtt|sub|idx|smi|ttml|dfxp)$/) print $4}' "$OUT_TSV" | head -20
  echo '```'
  echo
  echo "## 7. 其它扩展名文件样本（前 20 个）"
  echo
  echo '```text'
  awk -F'\t' '$1=="f"{e=tolower($4); if (e !~ /\.(mkv|mp4|avi|ts|m2ts|mov|wmv|flv|webm|iso|srt|ass|ssa|vtt|sub|idx|smi|ttml|dfxp)$/) print $4}' "$OUT_TSV" | head -20
  echo '```'
  echo
  echo "## 8. 最大的 20 个文件"
  echo
  echo "| 大小 | 路径 |"
  echo "| ---: | --- |"
  awk -F'\t' '$1=="f"{print $2"\t"$4}' "$OUT_TSV" | sort -t$'\t' -k1,1nr | head -20 | while IFS=$'\t' read -r b p; do printf '| %s | `%s` |\n' "$(human "$b")" "$p"; done
  echo
  echo "## 9. 路径最深的 15 个条目"
  echo
  echo '```text'
  awk -F'\t' '{n=gsub(/\//,"/",$4); print n"\t"$4}' "$OUT_TSV" | sort -t$'\t' -k1,1nr | head -15 | cut -f2
  echo '```'
} > "$OUT_MD"

echo "OK"
echo "MD : $OUT_MD  ($(wc -l < "$OUT_MD") 行, $(du -h "$OUT_MD" | cut -f1))"
echo "TSV: $OUT_TSV ($(wc -l < "$OUT_TSV") 行, $(du -h "$OUT_TSV" | cut -f1))"
