"""日志：控制台 + 滚动文件，双通道，每行带 run_id / 事件码 / 关键字段。

格式（FR-09）：
    时间 | 级别 | run_id | 事件码 | 消息 | 关键字段
文件：logs/jf-ingest.log（10 MiB × 5，UTF-8）
API Key 等敏感信息由调用方负责脱敏后再传进来。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_FILE_FORMAT = "%(asctime)s | %(levelname)s | %(run_id)s | %(event)s | %(message)s | %(fields)s"
_CONSOLE_FORMAT = "%(levelname)s | %(event)s | %(message)s"


class _Adapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("run_id", self.extra.get("run_id", "-"))
        extra.setdefault("event", "-")
        extra.setdefault("fields", "")
        return msg, kwargs


class EventLogger:
    """薄封装：logger.event("MOVE_OK", "已移动", src=..., dst=...)。"""

    def __init__(self, log_dir: str | Path, verbose: bool = False, run_id: str = "-") -> None:
        self.run_id = run_id
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger(f"jf-ingest.{run_id}")
        logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        logger.propagate = False
        logger.handlers.clear()

        fmt = logging.Formatter(_FILE_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
        file_handler = RotatingFileHandler(
            self.log_dir / "jf-ingest.log", maxBytes=10 * 1024 * 1024,
            backupCount=5, encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.DEBUG if verbose else logging.INFO)
        console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
        logger.addHandler(console)

        self._logger = _Adapter(logger, {"run_id": run_id})
        self.log_path = self.log_dir / "jf-ingest.log"

    @staticmethod
    def _fields(kwargs: dict[str, Any]) -> str:
        return " ".join(f"{k}={v}" for k, v in kwargs.items())

    def event(self, code: str, message: str, **fields: Any) -> None:
        self._logger.info(message, extra={"event": code, "fields": self._fields(fields)})

    def debug(self, code: str, message: str, **fields: Any) -> None:
        self._logger.debug(message, extra={"event": code, "fields": self._fields(fields)})

    def warning(self, code: str, message: str, **fields: Any) -> None:
        self._logger.warning(message, extra={"event": code, "fields": self._fields(fields)})

    def error(self, code: str, message: str, **fields: Any) -> None:
        self._logger.error(message, extra={"event": code, "fields": self._fields(fields)})

    def exception(self, code: str, message: str, **fields: Any) -> None:
        self._logger.exception(message, extra={"event": code, "fields": self._fields(fields)})

    def close(self) -> None:
        for handler in list(self._logger.logger.handlers):
            handler.flush()
            handler.close()
            self._logger.logger.removeHandler(handler)
