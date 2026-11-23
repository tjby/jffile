"""执行引擎：落盘、幂等、冲突、版本、安全 guards。"""

from __future__ import annotations

import os

import pytest

from jf_ingest import analyze, config, executor, media, naming, report
from jf_ingest.logutil import EventLogger


@pytest.fixture()
def layout(tmp_path):
    root = tmp_path / "root"
    source = root / "uploads"
    movies = root / "movies"
    tvshows = root / "tvshows"
    logs = root / "logs"
    for d in (source, movies, tvshows, logs):
        d.mkdir(parents=True)
    cfg = config.load()
    cfg["paths"].update({
        "source": str(source), "movies": str(movies),
        "tvshows": str(tvshows), "reports": str(logs),
    })
    cfg["jellyfin"]["refresh"] = False
    cfg["manifest"]["enabled"] = False
    logger = EventLogger(logs, run_id="test")
    yield cfg, source, movies, tvshows, logs, logger
    logger.close()


def _movie_plan(cfg, filename="测试电影.2020.1080p.mkv", size=11):
    root = "测试电影 (2020)"
    entry = media.Entry(root=root, files=[media.FileInfo(f"{root}/{filename}", size)])
    return analyze.build(entry, cfg)


def test_move_movie_keeps_bytes_and_removes_source(layout):
    cfg, source, movies, _tv, _logs, logger = layout
    root = "测试电影 (2020)"
    rel = f"{root}/测试电影.2020.1080p.mkv"
    src = source / rel
    src.parent.mkdir()
    src.write_bytes(b"video-bytes")
    plan = _movie_plan(cfg)
    ex = executor.Executor(cfg, logger)
    res = ex.execute_entry(plan, 1)

    assert res.status == "success"
    assert not src.exists()
    dst = movies / root / "测试电影 (2020).mkv"
    assert dst.read_bytes() == b"video-bytes"
    assert res.files[0].actual == f"movies/{root}/测试电影 (2020).mkv"
    assert res.target_dirs == [f"movies/{root}"]


def test_second_apply_is_idempotent(layout):
    cfg, source, _movies, _tv, _logs, logger = layout
    (source / "测试剧集").mkdir()
    for n in (1, 2):
        (source / "测试剧集" / f"测试剧集 - 0{n}.mkv").write_bytes(f"v{n}".encode())
    entry = media.Entry(root="测试剧集", files=[
        media.FileInfo(f"测试剧集/测试剧集 - 0{n}.mkv", 2) for n in (1, 2)
    ])
    plan = analyze.build(entry, cfg)
    ex = executor.Executor(cfg, logger)
    first = ex.execute_entry(plan, 1)
    assert first.status == "success" and first.changed == 2

    second = ex.execute_entry(plan, 1)
    assert second.status == "skipped"
    assert all(f.status == "SKIPPED" for f in second.files)


def test_existing_identical_file_is_skip_same(layout):
    cfg, source, movies, _tv, _logs, logger = layout
    root = "测试电影 (2020)"
    rel = f"{root}/测试电影.2020.1080p.mkv"
    src = source / rel
    src.parent.mkdir()
    src.write_bytes(b"same")
    dst = movies / root / "测试电影 (2020).mkv"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"same")
    os.utime(dst, (src.stat().st_atime, src.stat().st_mtime))

    plan = analyze.build(media.Entry(root=root, files=[
        media.FileInfo(rel, 4, src.stat().st_mtime),
    ]), cfg)
    res = executor.Executor(cfg, logger).execute_entry(plan, 1)
    assert res.files[0].status == "SKIP_SAME"
    assert dst.read_bytes() == b"same"
    assert src.exists()


def test_conflict_never_overwrites(layout):
    cfg, source, movies, _tv, _logs, logger = layout
    root = "测试电影 (2020)"
    rel = f"{root}/测试电影.2020.1080p.mkv"
    src = source / rel
    src.parent.mkdir()
    src.write_bytes(b"new")
    dst = movies / root / "测试电影 (2020).mkv"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"OLD")

    plan = analyze.build(media.Entry(root=root, files=[
        media.FileInfo(rel, 3),
    ]), cfg)
    res = executor.Executor(cfg, logger).execute_entry(plan, 1)
    assert res.status == "failed"
    assert res.files[0].status == "CONFLICT"
    assert dst.read_bytes() == b"OLD"
    assert src.exists()


def test_suffix_mode_and_version_label(layout):
    cfg, source, movies, _tv, _logs, logger = layout
    root = "测试电影 (2020)"
    rel = f"{root}/测试电影.2020.1080p.mkv"
    src = source / rel
    src.parent.mkdir()
    src.write_bytes(b"new")
    dst = movies / root / "测试电影 (2020).mkv"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"OLD")

    plan = _movie_plan(cfg)
    cfg["behavior"]["mode"] = "copy"
    cfg["behavior"]["on_conflict"] = "suffix"
    res = executor.Executor(cfg, logger).execute_entry(plan, 1)
    assert res.files[0].status == "VERSION_ADDED"
    assert res.files[0].actual.endswith("测试电影 (2020) - 1080p.mkv")
    assert dst.read_bytes() == b"OLD"

    (source / root / "测试电影.2020.2160p.mkv").write_bytes(b"4k")
    plan2 = _movie_plan(cfg, filename="测试电影.2020.2160p.mkv", size=2)
    res2 = executor.Executor(cfg, logger).execute_entry(plan2, 2)
    assert res2.files[0].status == "VERSION_ADDED"
    assert res2.files[0].actual.endswith("测试电影 (2020) - 2160p.mkv")


def test_copy_mode_keeps_source(layout):
    cfg, source, movies, _tv, _logs, logger = layout
    root = "测试电影 (2020)"
    rel = f"{root}/测试电影.2020.1080p.mkv"
    src = source / rel
    src.parent.mkdir()
    src.write_bytes(b"keep")
    plan = _movie_plan(cfg, size=4)
    cfg["behavior"]["mode"] = "copy"
    res = executor.Executor(cfg, logger).execute_entry(plan, 1)
    assert res.files[0].action == "copy"
    assert src.exists()
    assert (movies / root / "测试电影 (2020).mkv").read_bytes() == b"keep"


def test_hardlink_mode(layout):
    cfg, source, movies, _tv, _logs, logger = layout
    root = "测试电影 (2020)"
    rel = f"{root}/测试电影.2020.1080p.mkv"
    src = source / rel
    src.parent.mkdir()
    src.write_bytes(b"linked")
    plan = _movie_plan(cfg, size=6)
    cfg["behavior"]["mode"] = "hardlink"
    res = executor.Executor(cfg, logger).execute_entry(plan, 1)
    assert res.files[0].action == "link"
    assert src.exists()
    assert (movies / root / "测试电影 (2020).mkv").stat().st_ino == src.stat().st_ino


def test_path_guards(layout):
    cfg, source, _movies, _tv, _logs, logger = layout
    ex = executor.Executor(cfg, logger)
    with pytest.raises(executor.PathError):
        ex.source_path("../../etc/passwd")
    with pytest.raises(executor.PathError):
        ex.target_path("uploads/evil.mkv")
    with pytest.raises(executor.PathError):
        ex.target_path("movies/../../evil.mkv")

    fr = ex.place("测试剧集/测试剧集 - 01.mkv", "tvshows/../evil.mkv")
    assert fr.status == "ERROR"


def test_manual_and_new_entries_are_not_moved(layout):
    cfg, source, movies, _tv, _logs, logger = layout
    src = source / "Game" / "readme.txt"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    entry = media.Entry(root="Game", files=[media.FileInfo("Game/readme.txt", 1)])
    plan = analyze.build(entry, cfg)
    assert report.detail_group(plan) == "new"

    res = executor.Executor(cfg, logger).execute_entry(plan, 1)
    assert res.status == "skipped"
    assert src.exists()
    assert not (movies / "Game").exists()


def test_multipart_movie_is_left_untouched(layout):
    cfg, source, movies, _tv, _logs, logger = layout
    root = "测试电影 (2020)"
    (source / root).mkdir()
    names = [f"{root}1.mkv", f"{root}2.mkv"]
    for name in names:
        (source / root / name).write_bytes(b"v")
    plan = analyze.build(media.Entry(root=root, files=[
        media.FileInfo(f"{root}/{name}", 1) for name in names
    ]), cfg)
    assert "MULTI_PART" in plan.issues
    assert report.detail_group(plan) == "manual"
    res = executor.Executor(cfg, logger).execute_entry(plan, 1)
    assert res.status == "skipped" and res.files == []
    assert all((source / root / name).exists() for name in names)
    assert not (movies / root).exists()


def test_subtitle_and_extra_land_in_item_dir(layout):
    cfg, source, movies, _tv, _logs, logger = layout
    root = "测试电影 (2020)"
    base = source / root
    base.mkdir()
    (base / "测试电影.2020.1080p.mkv").write_bytes(b"v")
    (base / "测试电影.2020.1080p.chs.srt").write_bytes(b"s")
    (base / "cover.jpg").write_bytes(b"i")
    entry = media.Entry(root=root, files=[
        media.FileInfo(f"{root}/测试电影.2020.1080p.mkv", 1),
        media.FileInfo(f"{root}/测试电影.2020.1080p.chs.srt", 1),
        media.FileInfo(f"{root}/cover.jpg", 1),
    ])
    plan = analyze.build(entry, cfg)
    assert [e.kind for e in plan.extras] == ["cover"]
    res = executor.Executor(cfg, logger).execute_entry(plan, 1)

    folder = movies / root
    assert (folder / "测试电影 (2020).mkv").exists()
    assert (folder / "测试电影 (2020).chs.srt").exists()
    assert (folder / "cover.jpg").exists()
    assert all(f.success for f in res.files)


def test_prune_empty_dirs(layout):
    cfg, source, movies, _tv, _logs, logger = layout
    root = "测试电影 (2020)"
    rel = f"{root}/sub/测试电影.2020.1080p.mkv"
    src = source / rel
    src.parent.mkdir(parents=True)
    src.write_bytes(b"v")
    plan = analyze.build(media.Entry(root=root, files=[media.FileInfo(rel, 1)]), cfg)
    cfg["behavior"]["prune_empty_dirs"] = True
    res = executor.Executor(cfg, logger).execute_entry(plan, 1)
    assert res.status == "success"
    assert not (source / root).exists()
    assert (movies / root / "测试电影 (2020).mkv").exists()


def test_naming_version_helpers():
    assert naming.version_from_name("Film.2020.2160p.HDR.mkv") == "2160p"
    assert naming.version_from_name("Film 加长版.mkv") == "加长版"
    assert naming.version_from_name("Film.cd1.mkv") == "cd1"
    assert naming.version_from_name("Film.mkv") is None
    assert naming.with_version("Film (2020)", ".mkv", "2160p") == "Film (2020) - 2160p.mkv"
