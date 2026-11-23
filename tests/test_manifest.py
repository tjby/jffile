"""FR-17：入库记录内容、增量合并、同名外来文件不覆盖。"""

from __future__ import annotations

from datetime import datetime

import pytest

from jf_ingest import config, manifest
from jf_ingest.logutil import EventLogger


@pytest.fixture()
def env(tmp_path):
    cfg = config.load()
    logger = EventLogger(tmp_path / "logs", run_id="manifest-test")
    yield tmp_path, cfg, logger
    logger.close()


def _data(**kw):
    defaults = dict(
        title="测试电影", kind="movie", year=2020, source_root="测试电影 (2020)",
        first_at="2026-01-01 00:00:00", last_at="2026-01-02 00:00:00", result="成功",
        original=["测试电影 (2020)/", "└── a.mkv"],
        current=["测试电影 (2020)/", "├── a.mkv", "└── jf-ingest.md"],
        rows=[manifest.MappingRow("a.mkv", "a.mkv", "OK")],
    )
    defaults.update(kw)
    return manifest.ManifestData(**defaults)


def test_tree_lines_and_strip_root():
    lines = manifest.tree_lines("Show", ["Show/a.mkv", "Show/subs/b.srt"])
    assert lines == ["Show/", "├── a.mkv", "└── subs/", "    └── b.srt"]
    assert manifest.strip_root("Show/subs/b.srt", "Show") == "subs/b.srt"
    assert manifest.strip_root("other.mkv", "Show") == "other.mkv"


def test_render_md_has_four_sections_and_mapping():
    text = manifest.render(_data(), "md")
    assert text.startswith("# 测试电影")
    assert manifest.MD_MARKER in text
    for heading in ("## 原始结构", "## 当前结构", "## 改名对照"):
        assert heading in text
    assert "| `a.mkv` | `a.mkv` | OK |" in text
    assert "首次建档：2026-01-01 00:00:00" in text
    assert "最后更新：2026-01-02 00:00:00" in text


def test_render_txt_mapping_uses_double_dash():
    text = manifest.render(_data(), "txt")
    assert manifest.TXT_MARKER in text
    assert "a.mkv  --  a.mkv" in text


def test_write_and_incremental_merge(env):
    out, cfg, logger = env
    item = out / "测试电影 (2020)"
    item.mkdir()
    now = datetime(2026, 1, 2, 3, 4, 5)
    name, code = manifest.write(item, _data(), cfg, logger, now)
    assert name == "jf-ingest.md" and code == "OK"
    first = (item / name).read_text(encoding="utf-8")

    second_data = _data(
        last_at="2026-02-03 00:00:00",
        rows=[
            manifest.MappingRow("a.mkv", "a.mkv", "SKIP_SAME", "已存在且一致"),
            manifest.MappingRow("b.mkv", "b.mkv", "OK"),
        ],
    )
    manifest.write(item, second_data, cfg, logger, now)
    merged = (item / name).read_text(encoding="utf-8")
    assert "首次建档：2026-01-01 00:00:00" in merged
    assert "最后更新：2026-02-03 00:00:00" in merged
    assert "| `a.mkv` | `a.mkv` | SKIP_SAME：已存在且一致 |" in merged
    assert "| `b.mkv` | `b.mkv` | OK |" in merged
    assert merged.count("| `a.mkv` |") == 1  # 同当前路径不重复
    assert first != merged


def test_foreign_same_name_is_never_overwritten(env):
    out, cfg, logger = env
    item = out / "测试电影 (2020)"
    item.mkdir()
    foreign = item / "jf-ingest.md"
    foreign.write_text("# 我自己的笔记", encoding="utf-8")
    now = datetime(2026, 1, 2, 3, 4, 5)
    name, code = manifest.write(item, _data(), cfg, logger, now)
    assert name == "jf-ingest.20260102-030405.md"
    assert code == "MANIFEST_CONFLICT"
    assert foreign.read_text(encoding="utf-8") == "# 我自己的笔记"
    assert (item / name).exists()


def test_parse_existing_ignores_foreign_text():
    assert manifest.parse_existing("# 我自己的笔记")[0] == ""
    assert manifest.is_generated("# 我自己的笔记") is False


def test_include_skipped_false_filters_rows(env):
    out, cfg, logger = env
    item = out / "测试电影 (2020)"
    item.mkdir()
    data = _data(rows=[
        manifest.MappingRow("a.mkv", "a.mkv", "OK"),
        manifest.MappingRow("note.txt", "", "SKIPPED", "未安排目标"),
    ])
    cfg["manifest"]["include_skipped"] = False
    manifest.write(item, data, cfg, logger, datetime.now())
    text = (item / "jf-ingest.md").read_text(encoding="utf-8")
    assert "a.mkv" in text and "note.txt" not in text


def test_disabled_manifest_writes_nothing(env):
    out, cfg, logger = env
    item = out / "x"
    item.mkdir()
    cfg["manifest"]["enabled"] = False
    assert manifest.write(item, _data(), cfg, logger, datetime.now()) == ("", "MANIFEST_DISABLED")
    assert list(item.iterdir()) == []
