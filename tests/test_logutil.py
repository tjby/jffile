"""日志：run_id / 事件码 / 关键字段 / 级别 / 脱敏后的 key。"""

from __future__ import annotations

import pytest

from jf_ingest import jellyfin
from jf_ingest.logutil import EventLogger


@pytest.fixture()
def logger(tmp_path):
    obj = EventLogger(tmp_path / "logs", run_id="20260101-000000-abc123", verbose=True)
    yield obj, tmp_path / "logs" / "jf-ingest.log"
    obj.close()


def test_event_writes_all_fields(logger):
    obj, path = logger
    obj.event("MOVE_OK", "已移动", src="a.mkv", dst="movies/a.mkv")
    obj.warning("CHOWN_SKIP", "跳过属主", owner="jellyfin:jellyfin")
    obj.error("CONFLICT", "冲突未覆盖", dst="x.mkv")

    text = path.read_text(encoding="utf-8")
    assert "20260101-000000-abc123" in text
    assert "MOVE_OK | 已移动 | src=a.mkv dst=movies/a.mkv" in text
    run_id = "20260101-000000-abc123"
    assert f"WARNING | {run_id} | CHOWN_SKIP | 跳过属主 | owner=jellyfin:jellyfin" in text
    assert f"ERROR | {run_id} | CONFLICT | 冲突未覆盖" in text


def test_key_is_masked_before_logging(logger):
    obj, path = logger
    key = jellyfin.api_key({"jellyfin": {"api_key": "super-secret"}})
    obj.event("REFRESH_OK", "刷新成功", api_key=jellyfin.mask_key(key))
    text = path.read_text(encoding="utf-8")
    assert "super-secret" not in text
    assert "api_key=***" in text


def test_close_is_idempotent(logger):
    obj, _path = logger
    obj.close()
    obj.close()
