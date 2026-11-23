"""apply 端到端：确认表 → 落盘 → 入库记录 → 报告；幂等、冲突、退出码。"""

from __future__ import annotations

import argparse
import json

from jf_ingest import analyze, apply, cli, config, media, report


def _layout(tmp_path):
    root = tmp_path / "root"
    source = root / "uploads"
    for d in (source, root / "movies", root / "tvshows", root / "logs"):
        d.mkdir(parents=True)
    cfg = config.load()
    cfg["paths"].update({
        "source": str(source), "movies": str(root / "movies"),
        "tvshows": str(root / "tvshows"), "reports": str(root / "logs"),
    })
    cfg["jellyfin"]["refresh"] = False
    return cfg, root, source


def _preview(cfg, roots):
    plans = []
    for root in roots:
        files = [media.FileInfo(f"{root}/{root}.mkv", 6)]
        plans.append(analyze.build(media.Entry(root=root, files=files), cfg))
    rep = report.Reporter(cfg, cfg["paths"]["source"], {"files": len(plans)}, plans)
    return rep.write_all(cfg["paths"]["reports"]), plans


def _args(paths, **extra):
    values = {"plan": str(paths["tsv"]), "only": None, "from_listing": None, "verbose": False}
    values.update(extra)
    return argparse.Namespace(**values)


def test_apply_end_to_end_and_idempotency(tmp_path, capsys):
    cfg, root, source = _layout(tmp_path)
    name = "测试电影 (2020)"
    (source / name).mkdir()
    (source / name / f"{name}.mkv").write_bytes(b"movie")
    paths, _ = _preview(cfg, [name])

    assert apply.run_apply(cfg, _args(paths)) == 0
    target = root / "movies" / name / f"{name}.mkv"
    assert target.read_bytes() == b"movie"
    assert not (source / name / f"{name}.mkv").exists()

    manifest = root / "movies" / name / "jf-ingest.md"
    text = manifest.read_text(encoding="utf-8")
    for heading in ("## 原始结构", "## 当前结构", "## 改名对照"):
        assert heading in text
    assert "首次建档：" in text and "改名对照" in text

    apply_files = sorted((root / "logs").glob("*-apply.*"))
    assert [p.suffix for p in apply_files] == [".json", ".md"]
    json_path = next(p for p in apply_files if p.suffix == ".json")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["stats"]["entries"] == {"success": 1}
    assert payload["stats"]["moved_bytes"] == 5
    assert payload["refresh"]["skipped"] is True

    log = (root / "logs" / "jf-ingest.log").read_text(encoding="utf-8")
    assert "RUN_START" in log and "RUN_END" in log and "MANIFEST_OK" in log

    # 第二次跑：源已不在，跳过但仍返回 0
    assert apply.run_apply(cfg, _args(paths)) == 0
    assert target.read_bytes() == b"movie"
    assert "首次建档：" in manifest.read_text(encoding="utf-8")


def test_apply_conflict_returns_1_and_keeps_both(tmp_path):
    cfg, root, source = _layout(tmp_path)
    name = "测试电影 (2020)"
    (source / name).mkdir()
    (source / name / f"{name}.mkv").write_bytes(b"NEW")
    target_dir = root / "movies" / name
    target_dir.mkdir(parents=True)
    (target_dir / f"{name}.mkv").write_bytes(b"OLD")
    paths, _ = _preview(cfg, [name])

    assert apply.run_apply(cfg, _args(paths)) == 1
    assert (target_dir / f"{name}.mkv").read_bytes() == b"OLD"
    assert (source / name / f"{name}.mkv").read_bytes() == b"NEW"
    apply_md = next((root / "logs").glob("*-apply.md"))
    assert "冲突未覆盖" in apply_md.read_text(encoding="utf-8")


def test_apply_only_selects_original_sequence(tmp_path):
    cfg, root, source = _layout(tmp_path)
    first, second = "测试电影甲 (2020)", "测试电影乙 (2021)"
    for name in (first, second):
        (source / name).mkdir()
        (source / name / f"{name}.mkv").write_bytes(name.encode("utf-8"))
    paths, _ = _preview(cfg, [first, second])

    assert apply.run_apply(cfg, _args(paths, only="2")) == 0
    assert (root / "movies" / second / f"{second}.mkv").exists()
    assert not (root / "movies" / first / f"{first}.mkv").exists()


def test_cli_apply_flags(tmp_path):
    cfg, root, source = _layout(tmp_path)
    name = "测试电影 (2020)"
    (source / name).mkdir()
    (source / name / f"{name}.mkv").write_bytes(b"copy")
    paths, _ = _preview(cfg, [name])
    cfg_path = root / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    code = cli.main([
        "apply", "--config", str(cfg_path), "--plan", str(paths["tsv"]),
        "--mode", "copy", "--no-manifest", "--no-refresh", "--manifest-format", "txt",
    ])
    assert code == 0
    assert (root / "movies" / name / f"{name}.mkv").read_bytes() == b"copy"
    assert (source / name / f"{name}.mkv").exists()  # copy 保留源文件
    assert not (root / "movies" / name / "jf-ingest.md").exists()


def test_copy_mode_rejects_insufficient_disk(tmp_path, monkeypatch):
    cfg, root, source = _layout(tmp_path)
    name = "测试电影 (2020)"
    (source / name).mkdir()
    (source / name / f"{name}.mkv").write_bytes(b"x" * 100)
    paths, _ = _preview(cfg, [name])
    args = _args(paths)
    cfg["behavior"]["mode"] = "copy"
    monkeypatch.setattr(apply.shutil, "disk_usage", lambda _p: type("U", (), {"free": 10})())

    assert apply.run_apply(cfg, args) == 2
    assert not (root / "movies" / name / f"{name}.mkv").exists()


def test_cli_config_error_and_bad_plan(tmp_path):
    _cfg, root, _source = _layout(tmp_path)
    assert cli.main(["apply", "--config", str(root / "missing.json"), "--plan", "x.tsv"]) == 2
    cfg = config.load()
    cfg["paths"]["reports"] = str(root / "logs")
    path = root / "config.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["apply", "--config", str(path), "--plan", str(root / "nope.tsv")]) == 2
