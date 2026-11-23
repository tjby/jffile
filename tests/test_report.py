import json

import pytest

from jf_ingest import analyze, report, scan


def _plans(entry_factory, config):
    entries = [
        entry_factory(
            "Movie (2020)",
            [("Movie.2020.1080p.mkv", 100)],
        ),
        entry_factory(
            "Show",
            [("Show - 01.mkv", 100), ("Show - 02.mkv", 100)],
        ),
    ]
    return [analyze.build(e, config) for e in entries]


def test_report_writes_three_files(entry_factory, config, tmp_path):
    plans = _plans(entry_factory, config)
    r = report.Reporter(config, "/data/jellyfin/uploads", {"files": 3}, plans)
    paths = r.write_all(tmp_path)
    assert paths["md"].exists() and paths["tsv"].exists() and paths["json"].exists()

    md = paths["md"].read_text(encoding="utf-8")
    assert "只读预览" in md
    assert "## 3. 必须人工干预" in md
    assert "## 4. 未定" in md
    assert "## 5. 逐条目明细" in md
    assert "movies/Movie (2020)/Movie (2020).mkv" in md
    assert "tvshows/Show/Season 01/Show S01E01.mkv" in md

    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert data["mode"] == "preview"
    assert len(data["entries"]) == 2

    tsv = paths["tsv"].read_text(encoding="utf-8").splitlines()
    assert tsv[0].startswith("序号\t类型")
    assert len(tsv) == 3


def test_report_refuses_to_write_inside_library(entry_factory, config, tmp_path):
    plans = _plans(entry_factory, config)
    config["paths"]["movies"] = str(tmp_path / "movies")
    r = report.Reporter(config, "src", {"files": 0}, plans)
    with pytest.raises(SystemExit):
        r.write_all(tmp_path / "movies" / "reports")


def test_tsv_has_one_row_per_entry(entry_factory, config, tmp_path):
    plans = _plans(entry_factory, config)
    paths = report.Reporter(config, "src", {"files": 0}, plans).write_all(tmp_path)
    rows = [line for line in paths["tsv"].read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 3
    assert rows[1].split("\t")[1] == "movie"
    assert rows[2].split("\t")[1] == "tv"


def test_listing_roundtrip(tmp_path, config):
    listing = tmp_path / "x.tsv"
    listing.write_text(
        "f\t100\t2026-01-01 00:00\tMovie (2020)/Movie.2020.mkv\n",
        encoding="utf-8",
    )
    files = scan.load_listing(listing).files
    plans = [analyze.build(e, config) for e in scan.build_entries(files)]
    assert plans[0].movie_target == "movies/Movie (2020)/Movie (2020).mkv"


def test_report_uses_chinese_issue_labels(entry_factory, config, tmp_path):
    entry = entry_factory(
        "[LoliHouse] Show 3rd Season [01-12]",
        [("[LoliHouse] Show - 01.mkv", 100), ("[LoliHouse] Show - 03.mkv", 100)],
    )
    plan = analyze.build(entry, config)
    plan.issues.append("EP_RANGE_SUSPECT")
    plan.issues = sorted(set(plan.issues))
    paths = report.Reporter(config, "src", {"files": 0}, [plan]).write_all(tmp_path)
    md = paths["md"].read_text(encoding="utf-8")
    assert "EP_RANGE_SUSPECT（集号范围异常）" in md
    # 机器可读的文件里仍然只放代码，方便脚本处理
    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert "EP_RANGE_SUSPECT" in data["entries"][0]["issues"]
    assert "集号范围异常" not in str(data["entries"][0]["issues"])


def test_report_splits_manual_and_undecided(entry_factory, config, tmp_path):
    """已定要人工的进 §3；无法确认的进 §4 未定；已确认的 EP_RANGE 不进未定。"""
    game = analyze.build(
        entry_factory("[ALI213] Some Game v1.0", [("setup.exe", 100)]), config
    )
    dup = analyze.build(
        entry_factory("[Group] Show", [("Show 01.mkv", 100), ("Show 01 v2.mkv", 100)]),
        config,
    )
    dup.issues = sorted(set(dup.issues + ["DUP_RELEASE"]))
    season = analyze.build(
        entry_factory("[Group] Show 2 [01-02]", [("Show 01.mkv", 100), ("Show 02.mkv", 100)]),
        config,
    )
    ep_range = analyze.build(
        entry_factory("[Group] Show 3 [04-05]", [("Show 04.mkv", 100), ("Show 05.mkv", 100)]),
        config,
    )
    ep_range.issues = sorted(set(ep_range.issues + ["EP_RANGE_SUSPECT"]))

    paths = report.Reporter(
        config, "src", {"files": 0}, [game, dup, season, ep_range]
    ).write_all(tmp_path)
    md = paths["md"].read_text(encoding="utf-8")
    manual = md.split("## 3. 必须人工干预", 1)[1].split("## 4. 未定", 1)[0]
    undecided = md.split("## 4. 未定", 1)[1].split("## 5. 逐条目明细", 1)[0]

    assert "[ALI213] Some Game v1.0" in manual
    assert "[Group] Show 2 [01-02]" in undecided
    # 集号范围异常已决定不重编号，只作为 §4 末尾的说明，不单列成未定小节
    assert "### 4.3" not in undecided
    # 重复 release 是「已决定人工处理」，不重复出现在「未定」
    assert "[Group] Show`" not in undecided
    assert "DUP_RELEASE" not in undecided


def test_manual_entries_are_marked_red_in_detail(entry_factory, config, tmp_path):
    """§5 明细里，§3 的必须人工条目要有红色/醒目标记，其余条目不要。"""
    manual = analyze.build(
        entry_factory("Game.v1.0", [("readme.txt", 10)]), config
    )
    normal = analyze.build(
        entry_factory(
            "In.the.Sign.of.the.Taurus.1974",
            [("In.the.Sign.of.the.Taurus.1974.1080p.mkv", 100)],
        ), config
    )
    paths = report.Reporter(config, "src", {"files": 0}, [manual, normal]).write_all(tmp_path)
    md = paths["md"].read_text(encoding="utf-8")
    detail = md.split("## 5. 逐条目明细", 1)[1]

    assert '<span style="color:red">🔴 **【从未见过的问题，需人工确认】**' in detail
    assert detail.count("【从未见过的问题，需人工确认】") == 1
    assert "【从未见过的问题，需人工确认】" not in detail.split("#### 2.", 1)[1]


def test_detail_is_split_into_three_groups(entry_factory, config, tmp_path):
    """§5 顺序：5.1 新问题 → 5.2 需要人工干涉和处理 → 5.3 能够自动处理。"""
    new = analyze.build(entry_factory("Game.v1.0", [("readme.txt", 10)]), config)
    manual = analyze.build(
        entry_factory(
            "[G] Show S01",
            [("Show 01.mkv", 100), ("Show 01 v2.mkv", 100)],
        ),
        config,
    )
    auto = analyze.build(
        entry_factory(
            "In.the.Sign.of.the.Taurus.1974",
            [("In.the.Sign.of.the.Taurus.1974.1080p.mkv", 100)],
        ),
        config,
    )
    paths = report.Reporter(config, "src", {"files": 0}, [new, manual, auto]).write_all(tmp_path)
    md = paths["md"].read_text(encoding="utf-8")
    detail = md.split("## 5. 逐条目明细", 1)[1]

    a = detail.index("### 5.1")
    b = detail.index("### 5.2")
    c = detail.index("### 5.3")
    d = detail.index("## 6.")
    assert a < b < c < d
    assert "Game.v1.0" in detail[a:b]
    assert "【从未见过的问题，需人工确认】" in detail[a:b]
    assert "[G] Show S01" in detail[b:c]
    assert "【需要人工干涉和处理】" in detail[b:c]
    assert "In.the.Sign.of.the.Taurus.1974" in detail[c:d]
    assert "【需要人工干涉和处理】" not in detail[c:d]


def test_half_episode_has_normal_target(entry_factory, config, tmp_path):
    entry = entry_factory(
        "[LoliHouse] Slime 3rd Season [48.5-72]",
        [
            ("[LoliHouse] Slime - 48.5 [WebRip].mkv", 100),
            ("[LoliHouse] Slime - 49 [WebRip].mkv", 100),
        ],
    )
    plan = analyze.build(entry, config)
    half = next(e for e in plan.episodes if e.half)
    assert half.target.endswith("Slime S03E48.5.mkv")
    paths = report.Reporter(config, "src", {"files": 0}, [plan]).write_all(tmp_path)
    md = paths["md"].read_text(encoding="utf-8")
    assert "S03E48.5" in md
    assert "待人工决定" not in md
