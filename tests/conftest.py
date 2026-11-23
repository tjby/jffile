import json

import pytest

from jf_ingest import media, scan


@pytest.fixture()
def entry_factory():
    """构造一个 Entry：files = [(相对路径, 字节数)]"""

    def make(root: str, files: list[tuple[str, int]]) -> media.Entry:
        return media.Entry(
            root=root,
            files=[media.FileInfo(f"{root}/{p}" if root else p, size) for p, size in files],
        )

    return make


@pytest.fixture()
def config():
    return json.loads(
        json.dumps(
            {
                "paths": {
                    "source": "/data/jellyfin/uploads",
                    "movies": "/data/jellyfin/movies",
                    "tvshows": "/data/jellyfin/tvshows",
                    "reports": "./reports",
                },
                "behavior": {"max_episodes_in_md": 5},
                "subtitle": {"enabled": True, "map_language": False},
            }
        )
    )


@pytest.fixture()
def listing_dir(tmp_path):
    """写一份 scan-upload.sh 格式的 TSV。"""

    def write(rows: list[tuple[str, int]]) -> str:
        lines = [f"f\t{size}\t2026-01-01 00:00\t{rel}" for rel, size in rows]
        path = tmp_path / "listing.tsv"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    return write


def test_scan_ignores_junk(listing_dir):
    path = listing_dir(
        [
            ("A/film.mkv", 100),
            ("A/film.mkv.part", 10),
            ("A/x.torrent", 10),
            (".hidden/x.mkv", 10),
        ]
    )
    result = scan.load_listing(path)
    assert [f.path for f in result.files] == ["A/film.mkv"]
    assert result.skipped_junk == 2
    assert result.skipped_hidden == 1


def test_build_entries_groups_by_top_dir(listing_dir):
    path = listing_dir(
        [
            ("A/film.mkv", 100),
            ("B/s1.mkv", 100),
            ("B/s2.mkv", 100),
            ("loose.mkv", 100),
        ]
    )
    entries = scan.build_entries(scan.load_listing(path).files)
    assert [e.root for e in entries] == ["A", "B", "loose.mkv"]
