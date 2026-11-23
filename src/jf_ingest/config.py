"""配置：JSON（服务器 Python 3.10 没有 tomllib，也不能装 tomli）。

优先级：命令行参数 > 配置文件 > 内置默认值。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "paths": {
        "source": "/data/jellyfin/uploads",
        "movies": "/data/jellyfin/movies",
        "tvshows": "/data/jellyfin/tvshows",
        "reports": "./logs",
    },
    "behavior": {
        # apply 的落盘策略；preview / apply 由子命令决定，mode 只控制「怎么放」
        "mode": "move",              # move | copy | hardlink
        "on_conflict": "skip",       # skip | suffix
        "prune_empty_dirs": False,
        "stable_seconds": 0,
        "keep_episode_title": True,
        # 报告里最多展示多少条集映射（完整内容始终在 JSON/TSV 里）
        "max_episodes_in_md": 5,
    },
    "subtitle": {
        "enabled": True,
        "map_language": False,
    },
    "manifest": {
        # FR-17：apply 时在条目文件夹里写 jf-ingest.md
        "enabled": True,
        "name": "jf-ingest",
        "format": "md",              # md | txt
        "merge": True,
        "include_skipped": True,
    },
    "permissions": {
        # 服务器已有默认 ACL（user:jellyfin:rwx），默认不需要 chown
        "enabled": False,
        "owner": "jellyfin:jellyfin",
        "dir_mode": "0755",
        "file_mode": "0644",
    },
    "jellyfin": {
        "refresh": True,             # 没配 api_key 时只告警，不影响迁移
        "refresh_mode": "paths",     # paths | all
        "url": "http://localhost:8096",
        "api_key": "",               # 也可从环境变量 JF_API_KEY 读取
        "timeout": 10,
    },
}


class ConfigError(Exception):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: str | Path | None = None) -> dict[str, Any]:
    """读取配置；文件不存在时用默认值（不报错，方便直接跑）。"""
    cfg = copy.deepcopy(DEFAULTS)
    if path is None:
        return cfg

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"配置文件不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON: {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是对象: {p}")
    return _deep_merge(cfg, data)


def apply_overrides(cfg: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """命令行参数覆盖（只覆盖非 None 的项）。"""
    flat = {k: v for k, v in overrides.items() if v is not None}
    for key, value in flat.items():
        section, _, name = key.partition(".")
        if name:
            cfg.setdefault(section, {})[name] = value
        else:
            cfg[key] = value
    return cfg
