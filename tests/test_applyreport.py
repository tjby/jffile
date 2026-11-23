"""apply 报告：Markdown 可读、JSON 可机读、统计一致。"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from jf_ingest import report
from jf_ingest.applyplan import ApplyPlan
from jf_ingest.applyreport import ApplyReporter
from jf_ingest.executor import EntryResult, FileResult
from jf_ingest.jellyfin import RefreshResult


def _result():
    return EntryResult(
        seq=2, root="测试电影 (2020)", kind="movie", title="测试电影", year=2020,
        season=None, status="success",
        files=[
            FileResult("a.mkv", "movies/A/a.mkv", "movies/A/a.mkv", "OK", size=100),
            FileResult("a.srt", "movies/A/a.srt", "movies/A/a.srt", "SKIP_SAME", size=1),
        ],
        target_dirs=["movies/测试电影 (2020)"],
        manifest_file="jf-ingest.md",
    )


def test_apply_report(tmp_path):
    cfg = {
        "paths": {
            "source": "/data/jellyfin/uploads",
            "movies": "/data/jellyfin/movies",
            "tvshows": "/data/jellyfin/tvshows",
        },
        "behavior": {"mode": "move", "on_conflict": "skip"},
    }
    started = datetime(2026, 1, 2, 3, 4, 5)
    rep = ApplyReporter(
        cfg, "20260102-030405-abc123", started,
        ApplyPlan(origin="/tmp/x-preview.json", warnings=["回退重扫"]),
        [_result()],
        RefreshResult(enabled=True, mode="paths", ok=True, message="HTTP 204"),
        finished=datetime(2026, 1, 2, 3, 4, 9),
    )
    paths = rep.write_all(tmp_path)
    assert paths["md"].name == "20260102-030405-apply.md"
    assert paths["json"].name == "20260102-030405-apply.json"

    md = paths["md"].read_text(encoding="utf-8")
    for heading in ("# jf-ingest 执行报告", "## 1. 结果总览", "## 2. 逐条目结果",
                    "## 3. Jellyfin 刷新", "## 4. 注意事项"):
        assert heading in md
    assert "2. 电影 ｜ 成功" in md
    assert "`a.mkv`" in md and "已落盘" in md
    assert "`jf-ingest.md`" in md
    assert "回退重扫" in md
    assert "成功 (paths)" in md or "paths" in md

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["run_id"] == "20260102-030405-abc123"
    assert payload["stats"]["entries"] == {"success": 1}
    assert payload["stats"]["files"] == {"OK": 1, "SKIP_SAME": 1}
    assert payload["stats"]["moved_bytes"] == 100
    assert payload["refresh"]["ok"] is True
    assert payload["warnings"] == ["回退重扫"]
    assert payload["entries"][0]["manifest_file"] == "jf-ingest.md"


def test_apply_report_guard_rejects_library(tmp_path):
    cfg = {
        "paths": {
            "source": "/data/jellyfin/uploads",
            "movies": str(tmp_path / "library"),
            "tvshows": str(tmp_path / "tv"),
        },
    }
    rep = ApplyReporter(cfg, "x", datetime.now(), ApplyPlan(), [], RefreshResult())
    with pytest.raises(SystemExit, match="拒绝写入"):
        rep.write_all(tmp_path / "library" / "logs")


def test_report_guard_shared_helper(tmp_path):
    cfg = {"paths": {"movies": str(tmp_path / "m"), "tvshows": str(tmp_path / "t")}}
    with pytest.raises(SystemExit, match="拒绝写入"):
        report.guard_report_dir(cfg, tmp_path / "m" / "logs")
