"""确认表读取：序号对齐、preview.json 明细、回退重扫。"""

from __future__ import annotations

import json

import pytest

from jf_ingest import applyplan, config, media, report, scan
from jf_ingest.logutil import EventLogger


@pytest.fixture()
def logs(tmp_path):
    logger = EventLogger(tmp_path / "logs", run_id="plan-test")
    yield tmp_path, logger
    logger.close()


def _plans(cfg):
    return [
        media.Entry(root="电影甲", files=[media.FileInfo("电影甲/电影甲.mkv", 10)]),
        media.Entry(root="电影乙", files=[media.FileInfo("电影乙/电影乙.mkv", 20)]),
    ]


def _movie(cfg, root):
    from jf_ingest import analyze

    return analyze.build(
        media.Entry(root=root, files=[media.FileInfo(f"{root}/{root}.mkv", 10)]), cfg,
    )


def _write_pair(logs, cfg, roots):
    out, _ = logs
    plans = [_movie(cfg, root) for root in roots]
    rep = report.Reporter(cfg, cfg["paths"]["source"], {"files": len(plans)}, plans)
    paths = rep.write_all(out)
    return paths, plans


def test_load_selected_rows_from_preview_json(logs):
    out, logger = logs
    cfg = config.load()
    paths, _ = _write_pair(logs, cfg, ["电影甲", "电影乙"])
    lines = paths["tsv"].read_text(encoding="utf-8").splitlines()
    root = out / "20260101-000000-plan.tsv"
    root.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    (out / "20260101-000000-preview.json").write_bytes(paths["json"].read_bytes())

    plan = applyplan.load(root, cfg, logger)
    assert plan.origin.endswith("preview.json")
    assert [p.root for p in plan.entries] == ["电影乙"]
    assert plan.seq_by_root == {"电影乙": 2}
    assert plan.entries[0].movie_source == "电影乙/电影乙.mkv"


def test_mismatched_root_is_rejected(logs):
    out, logger = logs
    cfg = config.load()
    paths, _ = _write_pair(logs, cfg, ["电影甲", "电影乙"])
    lines = paths["tsv"].read_text(encoding="utf-8").splitlines()
    bad = out / "tsv-plan.tsv"
    bad.write_text(
        "\n".join([lines[0], lines[2].replace("电影乙", "电影丙")]) + "\n",
        encoding="utf-8",
    )
    (out / "tsv-preview.json").write_bytes(paths["json"].read_bytes())
    with pytest.raises(applyplan.PlanError, match="源目录"):
        applyplan.load(bad, cfg, logger)


def test_missing_preview_json_falls_back_to_listing(logs, listing_dir):
    out, logger = logs
    cfg = config.load()
    listing = listing_dir([
        ("电影甲/电影甲.mkv", 10),
        ("电影乙/电影乙.mkv", 20),
    ])
    roots = [e.root for e in scan.build_entries(scan.load_listing(listing).files)]
    expected = roots[1]
    root = out / "fallback-plan.tsv"
    root.write_text(
        "\n".join([
            "\t".join(["序号", "类型", "置信度", "源目录", "片名", "年份", "季",
                       "集范围", "正片", "花絮", "字幕", "目标", "待确认"]),
            "\t".join(["2", "movie", "high", expected, expected, "", "", "", "1",
                       "0", "0", f"movies/{expected}", ""]),
        ]) + "\n",
        encoding="utf-8",
    )
    plan = applyplan.load(root, cfg, logger, listing=listing)
    assert plan.origin == "rescan"
    assert [p.root for p in plan.entries] == [expected]
    assert plan.warnings


def test_confirmation_validation(logs):
    out, _logger = logs
    missing = out / "nope.tsv"
    with pytest.raises(applyplan.PlanError, match="不存在"):
        applyplan.read_confirmation(missing)

    bad = out / "bad.tsv"
    bad.write_text("序号\t类型\t源目录\t目标\nxx\tmovie\t电影甲\tmovies/电影甲\n", encoding="utf-8")
    with pytest.raises(applyplan.PlanError, match="非法序号"):
        applyplan.read_confirmation(bad)

    empty = out / "empty.tsv"
    empty.write_text("序号\t类型\t源目录\t目标\n", encoding="utf-8")
    with pytest.raises(applyplan.PlanError, match="空的"):
        applyplan.read_confirmation(empty)


def test_old_preview_json_is_rejected(logs):
    out, logger = logs
    cfg = config.load()
    paths, _ = _write_pair(logs, cfg, ["电影甲"])
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    payload["entries"][0].pop("movie_source", None)
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    confirmation = out / "old-plan.tsv"
    lines = paths["tsv"].read_text(encoding="utf-8").splitlines()
    confirmation.write_text("\n".join([lines[0], lines[1]]) + "\n", encoding="utf-8")
    (out / "old-preview.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(applyplan.PlanError, match="重新运行 preview"):
        applyplan.load(confirmation, cfg, logger)


def test_new_fields_are_present_in_preview_json(logs):
    out, _ = logs
    cfg = config.load()
    paths, _ = _write_pair(logs, cfg, ["电影甲"])
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    entry = payload["entries"][0]
    assert entry["seq"] == 1
    assert entry["source_files"] == ["电影甲/电影甲.mkv"]
    assert entry["movie_source"] == "电影甲/电影甲.mkv"
