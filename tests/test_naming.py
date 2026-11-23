from jf_ingest import naming


def test_movie_paths():
    assert naming.movie_folder("流浪地球2", 2023) == "流浪地球2 (2023)"
    assert naming.movie_file("流浪地球2", 2023, ".mkv") == "流浪地球2 (2023).mkv"


def test_movie_without_year_has_no_empty_parens():
    assert naming.movie_folder("流浪地球2", None) == "流浪地球2"
    assert naming.movie_file("流浪地球2", None, ".mkv") == "流浪地球2.mkv"
    assert "()" not in naming.movie_folder("片名", None)


def test_season_folder_always_two_digits():
    assert naming.season_folder(1) == "Season 01"
    assert naming.season_folder(12) == "Season 12"


def test_episode_file():
    assert (
        naming.episode_file("三体", 2023, 1, 2, ".mkv")
        == "三体 (2023) S01E02.mkv"
    )
    assert naming.episode_file("三体", None, 3, 12, ".mkv") == "三体 S03E12.mkv"


def test_episode_file_keeps_title():
    assert (
        naming.episode_file("三体", 2023, 1, 2, ".mkv", episode_title="科学边界")
        == "三体 (2023) S01E02 科学边界.mkv"
    )


def test_subtitle_file_uses_dot_delimiter():
    assert naming.subtitle_file("片名 (2023)", ["sc"], ".ass") == "片名 (2023).sc.ass"
    assert naming.subtitle_file("片名 (2023)", [], ".srt") == "片名 (2023).srt"


def test_reserved_chars_are_replaced():
    name = naming.movie_folder('a:b"c?', 2000)
    assert name == "a-b-c- (2000)"


def test_join():
    assert naming.join("movies", "A (2023)", "A (2023).mkv") == "movies/A (2023)/A (2023).mkv"
