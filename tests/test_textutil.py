import pytest

from jf_ingest import textutil as t


@pytest.mark.parametrize(
    "raw,title,year",
    [
        # 动画 RAW 包
        ("[DBD-Raws][卫宫家今天的饭][01-13TV全集][1080P][BDRip][HEVC-10bit][简繁外挂][FLAC][MKV]",
         "卫宫家今天的饭", None),
        ("[LoliHouse] Gachiakuta [01-24][WebRip 1080p HEVC-10bit AAC]", "Gachiakuta", None),
        ("[SAIO-Raws] プリズンスクール [BD 1920x1080 HEVC-10bit OPUS]", "プリズンスクール", None),
        # 西式 scene 命名
        ("The.Honey.Killer.2018.1080p.AMZN.WEB-DL.DDP2.0.H.264-NTG", "The Honey Killer", 2018),
        ("In.the.Sign.of.the.Taurus.1974", "In the Sign of the Taurus", 1974),
        ("Shame.2011.LIMITED.1080p.BluRay.X264-AMIABLE", "Shame", 2011),
        ("Lorna, the Exorcist (1974)", "Lorna, the Exorcist", 1974),
        # 站点水印
        ("tto@www.18p2p.com@三奸", "三奸", None),
        ("道@sexinsex.net@美女海滩Beach Heat Miami[13集全]", "美女海滩Beach Heat Miami", None),
        ("[www.bttop.net].陷阱：致命的誘惑 [中文字幕]", "陷阱：致命的誘惑", None),
        # 方括号里是片名、后面全是标签
        ("[威而钢之雄霸天下.未删减][1080P版HD-MP4-5.43G][国语中字][张文慈香港给力喜剧]",
         "威而钢之雄霸天下", None),
        ("[台湾三级] 欲望街车1992 [国语中文][高清修复版]GIF出处系列", "欲望街车", 1992),
        # 国家/地区标签不是片名
        ("[日本] [女囚处刑人][AVI-1G][日本超给力剧情]", "女囚处刑人", None),
        # 季号要从片名里剥离
        ("[BDrip] Link Click S02 [343-Labs][v2]", "Link Click", None),
        ("[LoliHouse] Tensei Shitara Slime Datta Ken 3rd Season [48.5-72][WebRip 1080p]",
         "Tensei Shitara Slime Datta Ken", None),
        # 点分片名 + 站点后缀
        ("【更多高清电影访问 www.mkvchina.com】圣母[中文字幕].Benedetta.2021.BluRay.1080p",
         "圣母", 2021),
    ],
)
def test_title_and_year(raw, title, year):
    got, _ = t.guess_title(raw)
    assert got == title
    assert t.parse_year(raw) == year


@pytest.mark.parametrize(
    "raw,season",
    [
        ("[BDrip] Link Click S02 [343-Labs][v2]", 2),
        ("[Lilith-Raws] Jaku-Chara Tomozaki-kun S02", 2),
        ("[MMSUB] Hikari no Ou 2nd Season", 2),
        ("平凡职业造就世界最强 第三季.2024.1080p", 3),
        ("Some Show Season 4", 4),
        ("Some Show", None),
    ],
)
def test_parse_season(raw, season):
    assert t.parse_season(raw) == season


def test_year_not_taken_from_resolution_or_filename_numbers():
    assert t.parse_year("[HYSUB]30-sai made Doutei[01-12][1920X1080]") is None
    assert t.parse_year("Movie 1080p x265") is None


def test_sanitize_reserved_chars():
    assert t.sanitize('a<b>c:d"e/f\\g|h?i*j') == "a-b-c-d-e-f-g-h-i-j"


def test_strip_known_ext_keeps_title_dots():
    assert t.strip_known_ext("美姐妹肉奴隶.Bishimai.Nikudorei") == "美姐妹肉奴隶.Bishimai.Nikudorei"
    assert t.strip_known_ext("film.mkv") == "film"
