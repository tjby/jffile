import pytest

from jf_ingest import analyze, detect


@pytest.mark.parametrize(
    "stem,season,episode,half",
    [
        ("Show S02E03", 2, 3, False),
        ("Show s1e12", 1, 12, False),
        ("[LoliHouse] Gachiakuta - 07 [WebRip]", None, 7, False),
        ("[LoliHouse] Gachiakuta - 48.5 [WebRip]", None, 48, True),
        ("[DBD-Raws] Title [03][1080P]", None, 3, False),
        ("[SAIO-Raws] プリズンスクール 07 [BD]", None, 7, False),
        ("剧名 第03集", None, 3, False),
        ("剧名 EP07 1080p", None, 7, False),
        # 十六进制哈希里不能把 E9BD 当集号
        ("[LY-RAW][BDRIP][Hensuki][08][x265_flac][C391E9BD]", None, 8, False),
        # 电影名里的 1080p / 年份不能被当成集号
        ("Movie.2019.1080p.BluRay.x264-GROUP", None, None, False),
        ("DRAGUSE", None, None, False),
    ],
)
def test_parse_episode(stem, season, episode, half):
    guess = detect.parse_episode(stem)
    assert guess.episode == episode
    assert guess.season == season
    assert guess.half is half


@pytest.mark.parametrize(
    "rel,is_extra",
    [
        ("剧/NCED 01.mkv", True),
        ("剧/SP/剧 SP01.mkv", True),
        ("剧/Extras/花絮.mkv", True),
        ("剧/[LY-RAW][BDRIP][Hensuki][cm][01][x265_flac][988A327A].mkv", True),
        # 发布说明里出现 OAD，不能把整个目录当花絮
        ("剧/TV+OAD_BDrip_1080P_x265/[VCB] Show [01].mkv", False),
        ("剧/Season 01/剧 S01E01.mkv", False),
    ],
)
def test_extra_detection(rel, is_extra):
    assert (detect.extra_kind(rel) is not None) is is_extra


def test_tv_episode_range_and_season(entry_factory):
    entry = entry_factory(
        "[LoliHouse] Gachiakuta [01-24][WebRip 1080p]",
        [(f"[LoliHouse] Gachiakuta - {i:02d} [WebRip].mkv", 100) for i in range(1, 25)],
    )
    result = detect.analyze(entry)
    assert result.kind == "tv"
    assert result.season == 1
    assert "SEASON_ASSUMED_1" in result.issues
    assert [int(n) for n in result.episode_numbers] == list(range(1, 25))


def test_tv_explicit_season_from_dir(entry_factory):
    entry = entry_factory(
        "[BDrip] Link Click S02 [343-Labs][v2]",
        [(f"Link Click S02E{i:02d}-[1080p].mkv", 100) for i in range(1, 13)],
    )
    result = detect.analyze(entry)
    assert result.kind == "tv"
    assert result.season == 2
    assert "SEASON_ASSUMED_1" not in result.issues


def test_movie_without_year(entry_factory):
    entry = entry_factory(
        "Draguse.Or.The.Infernal.Mansion.1976",
        [("Draguse.Or.The.Infernal.Mansion.1976.mp4", 100)],
    )
    result = detect.analyze(entry)
    assert result.kind == "movie"
    assert result.year == 1976
    assert result.title == "Draguse Or The Infernal Mansion"


def test_movie_title_from_filename_when_dir_is_bad(entry_factory):
    entry = entry_factory(
        "Nelly",
        [("耐莉.Nelly.2017.HD720P.X264.AAC.French.CHT.mp4", 100)],
    )
    result = detect.analyze(entry)
    assert result.kind == "movie"
    assert result.year == 2017


def test_half_episode_keeps_its_number(entry_factory, config):
    """17.5 和 17 一样处理：E17.5 原数字入库，不再是「待人工决定」。"""
    entry = entry_factory(
        "Everyday Host",
        [
            ("Everyday Host - 17 [WebRip].mkv", 100),
            ("Everyday Host - 17.5 [WebRip].mkv", 100),
        ],
    )
    plan = analyze.build(entry, config)
    assert "HALF_EPISODE" not in plan.issues
    assert all(e.target for e in plan.episodes)
    names = [e.target.rsplit("/", 1)[-1] for e in plan.episodes]
    assert names == [
        "Everyday Host S01E17.mkv",
        "Everyday Host S01E17.5.mkv",
    ]
    # SxxExx 写法里的 .5 也要保留
    entry2 = entry_factory(
        "[Group] Show S02 [01-02]",
        [("Show.S02E01.5.mkv", 100), ("Show.S02E02.mkv", 100)],
    )
    plan2 = analyze.build(entry2, config)
    assert any(e.target.endswith("Show S02E01.5.mkv") for e in plan2.episodes)


def test_season_in_filename_beats_default(entry_factory, config):
    """文件名明写第二季 / 3rd Season 时按实际季号，不再一律 S01。"""
    cases = [
        ("[G] Show", "[G] Show 第二季 - 01.mkv", 2),
        ("Third Season", "[G] Show 3rd Season - 02.mkv", 3),
        ("Season Word", "[G] Show Season 4 - 03.mkv", 4),
    ]
    for root, filename, want in cases:
        plan = analyze.build(entry_factory(root, [(filename, 100)]), config)
        assert plan.season == want
        assert "SEASON_ASSUMED_1" not in plan.issues
        assert f"S{want:02d}E" in plan.episodes[0].target


def test_episode_numbers_are_kept_not_renumbered(entry_factory, config):
    """04/05/06 就存成 S01E04/05/06，只按数字排序，不重编号、不补 01-03。"""
    entry = entry_factory(
        "[Group] Some Show [04-06]",
        [("Some Show 06.mkv", 100), ("Some Show 04.mkv", 100), ("Some Show 05.mkv", 100)],
    )
    plan = analyze.build(entry, config)
    assert [e.episode for e in plan.episodes] == [4, 5, 6]
    assert [e.target.split("/")[-1] for e in plan.episodes] == [
        "Some Show S01E04.mkv",
        "Some Show S01E05.mkv",
        "Some Show S01E06.mkv",
    ]
    # 只标记提醒，不阻止入库
    assert "EP_RANGE_SUSPECT" in plan.issues
    assert plan.kind == "tv" and all(e.target for e in plan.episodes)


def test_duplicate_release_flagged(entry_factory):
    entry = entry_factory(
        "[LY-RAW][BDRIP][Hensuki]",
        [
            ("[LY-RAW][BDRIP][Hensuki][01][x265_flac][AAAA].mkv", 100),
            ("[KTXP][Hensuki][01][BIG5][1080p].mkv", 100),
            ("[LY-RAW][BDRIP][Hensuki][02][x265_flac][BBBB].mkv", 100),
        ],
    )
    result = detect.analyze(entry)
    assert "DUP_RELEASE" in result.issues


def test_not_media(entry_factory):
    entry = entry_factory("Game.v1.0", [("readme.txt", 10)])
    result = detect.analyze(entry)
    assert result.kind == "unknown"
    assert "NO_VIDEO" in result.issues
