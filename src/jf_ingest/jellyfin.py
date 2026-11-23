"""Jellyfin 刷新（FR-12）：只依赖标准库 urllib，失败只告警，不影响迁移结果。

- paths（默认）：POST /Library/Media/Updated
  {"Updates":[{"Path":"<目标路径>","UpdateType":"Created"}]}
- all：POST /Library/Refresh
- 认证：Authorization: MediaBrowser Token="<api_key>"
- Key 来源：配置 jellyfin.api_key 或环境变量 JF_API_KEY；日志/报告里只显示 ***
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .logutil import EventLogger

MASK = "***"


@dataclass
class RefreshResult:
    enabled: bool = False
    mode: str = "paths"
    ok: bool = False
    skipped: bool = False
    message: str = ""
    probe_done: bool = False
    probe_ok: bool = False
    paths: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.skipped:
            return "未执行（" + self.message + "）"
        return "成功" if self.ok else "失败（" + self.message + "）"


def api_key(cfg: dict[str, Any]) -> str:
    value = cfg.get("jellyfin", {}).get("api_key") or os.environ.get("JF_API_KEY", "")
    return str(value).strip().strip('"').strip("'")


def mask_key(key: str) -> str:
    """永不输出明文；返回值只用于显示。"""
    return MASK if key else ""


def _url(cfg: dict[str, Any], path: str) -> str:
    base = str(cfg.get("jellyfin", {}).get("url", "http://localhost:8096")).rstrip("/")
    return base + path


def _timeout(cfg: dict[str, Any]) -> float:
    try:
        return float(cfg.get("jellyfin", {}).get("timeout", 10))
    except (TypeError, ValueError):
        return 10.0


def _request(url: str, method: str, key: str, timeout: float,
             body: dict[str, Any] | None = None) -> tuple[int, str]:
    data = None
    headers = {"Authorization": f'MediaBrowser Token="{key}"', "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(2048).decode("utf-8", "replace")


def probe(cfg: dict[str, Any], logger: EventLogger) -> bool:
    """apply 开始前探测连通性；失败只提示「刷新会失败，但迁移继续」。"""
    key = api_key(cfg)
    if not key or not cfg.get("jellyfin", {}).get("refresh", True):
        return False
    url = _url(cfg, "/System/Info/Public")
    try:
        status, _ = _request(url, "GET", key, _timeout(cfg))
        ok = 200 <= status < 300
        logger.event(
            "REFRESH_PROBE_OK" if ok else "REFRESH_FAILED",
            "Jellyfin 连通性探测" + ("正常" if ok else f"异常（HTTP {status}）"),
            api_key=mask_key(key),
        )
        return ok
    except (urllib.error.URLError, OSError) as exc:
        logger.warning(
            "REFRESH_FAILED",
            f"Jellyfin 不可达，刷新会失败但迁移继续：{type(exc).__name__}",
            api_key=mask_key(key),
        )
        return False


def refresh(cfg: dict[str, Any], paths: Iterable[str], logger: EventLogger) -> RefreshResult:
    jf = cfg.get("jellyfin", {})
    if not jf.get("refresh", True):
        return RefreshResult(enabled=True, skipped=True, message="--no-refresh")
    key = api_key(cfg)
    if not key:
        return RefreshResult(
            enabled=True, skipped=True,
            message="未配置 api_key（jellyfin.api_key 或 JF_API_KEY）",
        )

    mode = str(jf.get("refresh_mode", "paths")).lower()
    targets = sorted({p for p in paths if p})
    result = RefreshResult(enabled=True, mode=mode, paths=targets)

    if mode == "all":
        url = _url(cfg, "/Library/Refresh")
        body = None
    else:
        mode = "paths"
        url = _url(cfg, "/Library/Media/Updated")
        body = {"Updates": [
            {"Path": p, "UpdateType": "Created"} for p in targets
        ]}
    result.mode = mode

    if mode == "paths" and not targets:
        result.skipped = True
        result.message = "没有需要刷新的目标路径"
        return result

    try:
        status, detail = _request(url, "POST", key, _timeout(cfg), body)
        if 200 <= status < 300:
            result.ok = True
            result.message = f"HTTP {status}"
            logger.event(
                "REFRESH_OK", f"Jellyfin 刷新成功（{mode}，{len(targets)} 个路径）",
                api_key=mask_key(key), status=status,
            )
        else:
            result.message = f"HTTP {status}: {detail[:120]}"
            logger.warning("REFRESH_FAILED", "Jellyfin 刷新失败：" + result.message,
                           api_key=mask_key(key))
    except (urllib.error.URLError, OSError) as exc:
        result.message = f"{type(exc).__name__}: {exc}"
        logger.warning("REFRESH_FAILED", "Jellyfin 刷新失败：" + result.message,
                       api_key=mask_key(key))
    return result


def validate(cfg: dict[str, Any]) -> str | None:
    """配置错误返回说明；调用方转成退出码 2。"""
    jf = cfg.get("jellyfin", {})
    mode = str(jf.get("refresh_mode", "paths")).lower()
    if mode not in {"paths", "all"}:
        return f"jellyfin.refresh_mode 只能是 paths / all，当前为 {mode!r}"
    return None


__all__ = ["RefreshResult", "api_key", "mask_key", "refresh", "probe", "validate"]
