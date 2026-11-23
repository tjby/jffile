"""Jellyfin 刷新：URL / 请求体 / 认证头 / 失败只告警。"""

from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from jf_ingest import config, jellyfin
from jf_ingest.logutil import EventLogger


class FakeResponse:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _n=0):
        return b"ok"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = config.load()
    cfg["jellyfin"].update({
        "refresh": True, "refresh_mode": "paths", "url": "http://localhost:8096/",
        "api_key": "secret-token", "timeout": 2,
    })
    monkeypatch.delenv("JF_API_KEY", raising=False)
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append({
            "url": req.full_url, "method": req.get_method(),
            "headers": dict(req.header_items()),
            "body": req.data.decode("utf-8") if req.data else None,
            "timeout": timeout,
        })
        return FakeResponse()

    monkeypatch.setattr(jellyfin.urllib.request, "urlopen", fake_urlopen)
    logger = EventLogger(tmp_path / "logs", run_id="jf-test")
    yield cfg, logger, calls
    logger.close()


def test_api_key_and_mask(env, monkeypatch):
    cfg, _logger, _calls = env
    assert jellyfin.api_key(cfg) == "secret-token"
    assert jellyfin.mask_key(jellyfin.api_key(cfg)) == "***"
    cfg["jellyfin"]["api_key"] = ""
    monkeypatch.setenv("JF_API_KEY", "from-env")
    assert jellyfin.api_key(cfg) == "from-env"


def test_refresh_paths_posts_updates(env):
    cfg, logger, calls = env
    result = jellyfin.refresh(cfg, [
        "/data/jellyfin/movies/A", "/data/jellyfin/movies/A",
        "/data/jellyfin/tvshows/B",
    ], logger)
    assert result.ok and not result.skipped
    assert result.mode == "paths"
    assert len(result.paths) == 2
    call = calls[0]
    assert call["url"] == "http://localhost:8096/Library/Media/Updated"
    assert call["method"] == "POST"
    assert call["timeout"] == 2
    assert call["headers"]["Authorization"] == 'MediaBrowser Token="secret-token"'
    body = json.loads(call["body"])
    assert [u["Path"] for u in body["Updates"]] == [
        "/data/jellyfin/movies/A", "/data/jellyfin/tvshows/B",
    ]
    assert all(u["UpdateType"] == "Created" for u in body["Updates"])


def test_refresh_all_uses_library_refresh(env):
    cfg, logger, calls = env
    cfg["jellyfin"]["refresh_mode"] = "all"
    result = jellyfin.refresh(cfg, [], logger)
    assert result.ok and result.mode == "all"
    assert calls[0]["url"] == "http://localhost:8096/Library/Refresh"
    assert calls[0]["body"] is None


def test_refresh_failure_only_warns(env, monkeypatch):
    cfg, logger, _calls = env

    def boom(_req, timeout=None):
        raise URLError("connection refused")

    monkeypatch.setattr(jellyfin.urllib.request, "urlopen", boom)
    result = jellyfin.refresh(cfg, ["/data/jellyfin/movies/A"], logger)
    assert not result.ok and result.message
    assert not result.skipped
    assert "connection refused" not in result.message or "URLError" in result.message


def test_refresh_skips_without_key_or_flag(env):
    cfg, logger, calls = env
    cfg["jellyfin"]["api_key"] = ""
    result = jellyfin.refresh(cfg, ["/x"], logger)
    assert result.skipped and "api_key" in result.message
    cfg["jellyfin"]["api_key"] = "secret-token"
    cfg["jellyfin"]["refresh"] = False
    result = jellyfin.refresh(cfg, ["/x"], logger)
    assert result.skipped and "--no-refresh" in result.message
    assert calls == []


def test_probe_and_validate(env, monkeypatch):
    cfg, logger, calls = env
    assert jellyfin.probe(cfg, logger) is True
    assert calls[0]["url"].endswith("/System/Info/Public")
    assert calls[0]["method"] == "GET"

    def boom(_req, timeout=None):
        raise OSError("offline")

    monkeypatch.setattr(jellyfin.urllib.request, "urlopen", boom)
    assert jellyfin.probe(cfg, logger) is False
    assert jellyfin.validate(cfg) is None
    cfg["jellyfin"]["refresh_mode"] = "bogus"
    assert "refresh_mode" in jellyfin.validate(cfg)
