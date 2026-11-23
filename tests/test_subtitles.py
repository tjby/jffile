from jf_ingest import analyze, subtitles


def test_pairs_two_subtitles_with_tags(entry_factory, config):
    entry = entry_factory(
        "[DBD-Raws][卫宫家今天的饭][01-13TV全集]",
        [
            ("[DBD-Raws][Emiya][01][1080P][FLAC].mkv", 100),
            ("[DBD-Raws][Emiya][01][1080P][FLAC].sc.ass", 10),
            ("[DBD-Raws][Emiya][01][1080P][FLAC].tc.ass", 10),
        ],
    )
    plan = analyze.build(entry, config)
    assert plan.kind == "tv"
    subs = [s for s in plan.subs if s.status == "same_dir"]
    assert len(subs) == 2
    targets = {s.target for s in subs}
    assert all(t.startswith("tvshows/") for t in targets)
    # 字幕要跟着「目标视频名」而不是源文件名
    assert any(t.endswith(" S01E01.sc.ass") for t in targets)
    assert any(t.endswith(" S01E01.tc.ass") for t in targets)
    assert len(targets) == 2


def test_movie_subtitle_uses_target_stem(entry_factory, config):
    entry = entry_factory(
        "Draguse.Or.The.Infernal.Mansion.1976",
        [
            ("Draguse.Or.The.Infernal.Mansion.1976.mp4", 100),
            ("Draguse.Or.The.Infernal.Mansion.1976.srt", 10),
        ],
    )
    plan = analyze.build(entry, config)
    sub = plan.subs[0]
    assert sub.target == (
        "movies/Draguse Or The Infernal Mansion (1976)/"
        "Draguse Or The Infernal Mansion (1976).srt"
    )


def test_subtitle_in_subdir_matched_by_episode(entry_factory, config):
    entry = entry_factory(
        "[orion origin] Majo to Yajuu [01-12]",
        [
            ("[orion origin] Majo to Yajuu [01] [1080p].mkv", 100),
            ("1-12 sup/[orion origin] Majo to Yajuu [01] [1080p].sup", 10),
        ],
    )
    plan = analyze.build(entry, config)
    assert "SUBS_IN_SUBDIR" in plan.issues
    sub = next(s for s in plan.subs if s.status == "subdir")
    assert sub.target.endswith("Majo to Yajuu S01E01.sup")


def test_unpaired_subtitle_is_reported(entry_factory, config):
    entry = entry_factory(
        "Lorna, the Exorcist (1974)",
        [
            ("Les possédées du diable (1974).mp4", 100),
            ("Les possédées du diable (1974) .CHS.srt", 10),
        ],
    )
    plan = analyze.build(entry, config)
    assert any(s.status == "unpaired" for s in plan.subs)
    assert "SUBS_UNPAIRED" in plan.issues


def test_archive_with_subtitles_is_hinted(entry_factory, config):
    entry = entry_factory(
        "[DMG] Show [01-12]",
        [
            ("[DMG] Show - 01.mkv", 100),
            ("[DMG] Show - Subtitles.7z", 100),
            ("[DMG] Show - Fonts.7z", 100),
        ],
    )
    plan = analyze.build(entry, config)
    archive_subs = [s for s in plan.subs if s.status == "archive"]
    assert [s.source for s in archive_subs] == ["[DMG] Show [01-12]/[DMG] Show - Subtitles.7z"]
    assert "SUBS_IN_ARCHIVE" in plan.issues


def test_vobsub_pair_detected(entry_factory):
    entry = entry_factory(
        "Movie (2000)",
        [
            ("Movie (2000).mkv", 100),
            ("Movie (2000).idx", 10),
            ("Movie (2000).sub", 10),
        ],
    )
    from jf_ingest import detect

    analysis = detect.analyze(entry)
    report = subtitles.analyze(entry, analysis)
    assert len(report.vobsub_pairs) == 1
