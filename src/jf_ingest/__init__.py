"""jf-ingest：把手工上传的影视文件整理成 Jellyfin 能识别的结构。

- `preview`：只读预览，不改动任何文件。
- `apply`：按确认表执行，默认 move，写 jf-ingest.md 入库记录，可选刷新 Jellyfin。
"""

__version__ = "0.4.0"
