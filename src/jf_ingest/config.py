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
        # 只读预览阶段：不移动、不改名、不 chown
        "mode": "preview",
        "stable_seconds": 0,
        "keep_episode_title": True,
        # 报告里最多展示多少条集映射（完整内容始终在 JSON/TSV 里）
        "max_episodes_in_md": 5,
    },
    "subtitle": {
        # 阶段一：字幕只记录、不改名
        "enabled": True,
        "map_language": False,
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
