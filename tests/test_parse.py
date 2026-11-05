"""File-name reading: hundreds of real-world naming styles."""

import os

import pytest

from rennix.parse import _guessit, parse_name, parse_path
from rennix.textnorm import normalize, similarity

# (file name, kind, title, year, season, episodes, extra checks)
CASES = [
    ("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv", "movie", "The Matrix", 1999, None, [], {"resolution": "1080p", "source": "BluRay", "video_codec": "H.264", "release_group": "GROUP"}),
    ("inception_2010_720p.mp4", "movie", "Inception", 2010, None, [], {"resolution": "720p"}),
    ("dune.mkv", "movie", "Dune", None, None, [], {}),
    ("Blade.Runner.2049.2017.2160p.UHD.BluRay.x265.HDR.DTS-HD.MA.7.1-SWTYBLZ.mkv", "movie", "Blade Runner 2049", 2017, None, [], {"resolution": "2160p", "video_codec": "HEVC"}),
    ("1917 (2019).mkv", "movie", "1917", 2019, None, [], {}),
    ("2012.mkv", "movie", "2012", None, None, [], {}),
    ("Wonder.Woman.1984.2020.1080p.WEB-DL.DDP5.1.Atmos.H.264-EVO.mkv", "movie", "Wonder Woman 1984", 2020, None, [], {"source": "WEB-DL"}),
    ("Avatar.2009.Extended.Directors.Cut.1080p.mkv", "movie", "Avatar", 2009, None, [], {"edition": "Director's Cut"}),
    ("Kill Bill Vol 1 (2003).mkv", "movie", "Kill Bill Vol 1", 2003, None, [], {}),
    ("The Lord of the Rings The Fellowship of the Ring 2001 Extended CD1.avi", "movie", "The Lord of the Rings The Fellowship of the Ring", 2001, None, [], {"part": 1, "edition": "Extended"}),
    ("Spiderman.2002.mkv", "movie", "Spiderman", 2002, None, [], {}),
    ("Rocky.II.1979.mkv", "movie", "Rocky II", 1979, None, [], {}),
    ("Apollo 13 (1995).mkv", "movie", "Apollo 13", 1995, None, [], {}),
    ("Star.Wars.Episode.IV.A.New.Hope.1977.mkv", "movie", "Star Wars Episode IV A New Hope", 1977, None, [], {}),
    ("Mission.Impossible.Dead.Reckoning.Part.One.2023.mkv", "movie", "Mission Impossible Dead Reckoning Part One", 2023, None, [], {}),
    ("2001 A Space Odyssey (1968).mkv", "movie", "2001 A Space Odyssey", 1968, None, [], {}),
    ("Se7en.1995.REMASTERED.1080p.mkv", "movie", "Se7en", 1995, None, [], {"edition": "Remastered"}),
    ("Movie.Title.2015.PROPER.REPACK.720p.mkv", "movie", "Movie Title", 2015, None, [], {}),
    ("the.dark.knight.2008.imax.2160p.mkv", "movie", "The Dark Knight", 2008, None, [], {"edition": "IMAX"}),
    ("Parasite.2019.KOREAN.1080p.WEBRip.mkv", "movie", "Parasite", 2019, None, [], {"source": "WEBRip"}),
    ("Amelie (2001) {tmdb-194}.mkv", "movie", "Amelie", 2001, None, [], {"ids": {"tmdb": 194}}),
    ("The Matrix (1999) [imdbid-tt0133093].mkv", "movie", "The Matrix", 1999, None, [], {"ids": {"imdb": "tt0133093"}}),
    ("Movie (2010) {edition-Director's Cut}.mkv", "movie", "Movie", 2010, None, [], {"edition": "Director's Cut"}),
    ("Breaking.Bad.S02E05.Breakage.720p.HDTV.x264-CTU.mkv", "episode", "Breaking Bad", None, 2, [5], {"episode_title": "Breakage"}),
    ("breaking bad s02e06 720p.mkv", "episode", "Breaking Bad", None, 2, [6], {}),
    ("the.office.us.s03e01e02.mkv", "episode", "The Office", None, 3, [1, 2], {"country": "US"}),
    ("The.Office.UK.S01E01.mkv", "episode", "The Office", None, 1, [1], {"country": "GB"}),
    ("doctor who 2005 s01e01 rose.mkv", "episode", "Doctor Who", 2005, 1, [1], {"episode_title": "Rose"}),
    ("Futurama.1x01.Space.Pilot.3000.mkv", "episode", "Futurama", None, 1, [1], {"episode_title": "Space Pilot 3000"}),
    ("Show.Name.S01E01-E03.mkv", "episode", "Show Name", None, 1, [1, 2, 3], {}),
    ("Show.Name.S01E01-03.mkv", "episode", "Show Name", None, 1, [1, 2, 3], {}),
    ("Show Name - 2x05-06.mkv", "episode", "Show Name", None, 2, [5, 6], {}),
    ("Show Name Season 2 Episode 7.mkv", "episode", "Show Name", None, 2, [7], {}),
    ("Lost.S00E05.Special.mkv", "episode", "Lost", None, 0, [5], {}),
    ("Grey's.Anatomy.S10E100.mkv", "episode", "Grey's Anatomy", None, 10, [100], {}),
    ("[SubsPlease] Naruto - 147 (1080p) [A1B2C3D4].mkv", "episode", "Naruto", None, None, [], {"absolute": 147, "release_group": "SubsPlease"}),
    ("One Piece - 1071.mkv", "episode", "One Piece", None, None, [], {"absolute": 1071}),
    ("The.Daily.Show.2026.03.14.Guest.Name.720p.WEB.h264.mkv", "episode", "The Daily Show", None, None, [], {"air_date": "2026-03-14"}),
    ("Last.Week.Tonight.2025-11-02.mkv", "episode", "Last Week Tonight", None, None, [], {"air_date": "2025-11-02"}),
    ("Stranger.Things.S04E09.Chapter.Nine.The.Piggyback.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-FLUX.mkv", "episode", "Stranger Things", None, 4, [9], {"resolution": "2160p"}),
    ("VID_00231.mp4", "movie", "Vid 00231", None, None, [], {"generic": True}),
]


@pytest.mark.parametrize("name,kind,title,year,season,episodes,extra", CASES, ids=[c[0][:40] for c in CASES])
def test_known_names(name, kind, title, year, season, episodes, extra):
    p = parse_name(name, engine="builtin")
    assert p.kind == kind
    assert normalize(p.title) == normalize(title), p.title
    assert p.year == year
    assert p.season == season
    assert p.episodes == episodes
    for key, val in extra.items():
        assert getattr(p, key) == val, f"{key}: {getattr(p, key)!r}"


MOVIES = [("The Matrix", 1999), ("Inception", 2010), ("Pulp Fiction", 1994), ("The Dark Knight", 2008),
          ("Spirited Away", 2001), ("Mad Max Fury Road", 2015), ("Toy Story", 1995), ("Parasite", 2019),
          ("Interstellar", 2014), ("Get Out", 2017), ("Mean Girls", 2004), ("Back to the Future", 1985),
          ("The Shawshank Redemption", 1994), ("Arrival", 2016), ("Gladiator", 2000), ("Her", 2013),
          ("Up", 2009), ("The Lord of the Rings The Two Towers", 2002), ("No Country for Old Men", 2007),
          ("Everything Everywhere All at Once", 2022)]
MOVIE_STYLES = [
    lambda t, y: f"{t.replace(' ', '.')}.{y}.1080p.BluRay.x264-GRP.mkv",
    lambda t, y: f"{t} ({y}).mkv",
    lambda t, y: f"{t.replace(' ', '_')}_{y}_720p.mp4",
    lambda t, y: f"{t} {y} 2160p UHD BluRay x265 HDR-GRP.mkv",
    lambda t, y: f"{t.lower().replace(' ', '.')}.{y}.web-dl.mkv",
    lambda t, y: f"{t} [{y}] [1080p] [YTS.MX].mp4",
]


@pytest.mark.parametrize("title,year", MOVIES)
@pytest.mark.parametrize("style", range(len(MOVIE_STYLES)))
def test_movie_styles(title, year, style):
    name = MOVIE_STYLES[style](title, year)
    p = parse_name(name, engine="builtin")
    assert p.kind == "movie", name
    assert normalize(p.title) == normalize(title), (name, p.title)
    assert p.year == year, name


SHOWS = ["Breaking Bad", "The Office", "Game of Thrones", "Stranger Things", "The Wire", "Friends",
         "Better Call Saul", "Doctor Who", "The Crown", "Peaky Blinders"]
EP_STYLES = [
    lambda t, s, e: f"{t.replace(' ', '.')}.S{s:02d}E{e:02d}.720p.HDTV.x264-GRP.mkv",
    lambda t, s, e: f"{t} - {s}x{e:02d} - Some Title.mkv",
    lambda t, s, e: f"{t.lower()} s{s:02d}e{e:02d}.mkv",
    lambda t, s, e: f"{t} Season {s} Episode {e}.mkv",
    lambda t, s, e: f"{t.replace(' ', '_')}_S{s}E{e:02d}_WEBRip.mp4",
]


@pytest.mark.parametrize("show", SHOWS)
@pytest.mark.parametrize("style", range(len(EP_STYLES)))
def test_episode_styles(show, style):
    s, e = (len(show) % 7) + 1, (len(show) * 3) % 22 + 1
    name = EP_STYLES[style](show, s, e)
    p = parse_name(name, engine="builtin")
    assert p.kind == "episode", name
    assert normalize(p.title) == normalize(show), (name, p.title)
    assert (p.season, p.episodes) == (s, [e]), name


@pytest.mark.parametrize("path,title,year,season,episodes", [
    ("TV Shows/The Office (US)/Season 3/03x01.mkv", "The Office", None, 3, [1]),
    ("Shows/Firefly/Season 1/Firefly - 01 - Serenity.mkv", "Firefly", None, 1, [1]),
    ("Shows/Lost/Season 2/203.mkv", "Lost", None, 2, [3]),
    ("Shows/Lost/Specials/Lost - The Answer.mkv", "Lost", None, 0, []),
])
def test_paths_with_season_folders(path, title, year, season, episodes):
    p = parse_path(path, engine="builtin")
    assert p.kind == "episode"
    assert normalize(p.title) == normalize(title)
    assert p.season == season
    if episodes:
        assert p.episodes == episodes


def test_movie_folder_fills_in_generic_file():
    p = parse_path("Movies/Inception (2010)/movie.mkv", engine="builtin")
    assert (p.kind, p.title, p.year) == ("movie", "Inception", 2010)


def test_movie_folder_supplies_year():
    p = parse_path("Downloads/Dune.2021/dune.mkv", engine="builtin")
    assert (p.title, p.year) == ("Dune", 2021)


def test_folder_ids():
    p = parse_path("Movies/The Matrix (1999) {tmdb-603}/The Matrix (1999).mkv", engine="builtin")
    assert p.ids.get("tmdb") == 603


def test_alternative_readings():
    p = parse_name("Wonder.Woman.1984.mkv", engine="builtin")
    assert ("Wonder Woman 1984", None) in p.alt_titles


def test_similarity_rules():
    assert similarity("Spiderman", "Spider-Man") == 1.0
    assert similarity("Amelie", "Amélie") == 1.0
    assert similarity("Rocky 2", "Rocky II") == 1.0
    assert similarity("Alien 3", "Alien³") == 1.0
    assert similarity("Fast and Furious", "Fast & Furious") == 1.0
    assert similarity("The Matrix", "Matrix") == 1.0
    assert similarity("Inception", "Interstellar") < 0.7


@pytest.mark.skipif(_guessit is None, reason="guessit not installed")
@pytest.mark.parametrize("name,kind,title", [
    ("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv", "movie", "The Matrix"),
    ("Breaking.Bad.S02E05.Breakage.720p.HDTV.x264-CTU.mkv", "episode", "Breaking Bad"),
    ("Star.Wars.Episode.IV.A.New.Hope.1977.mkv", "movie", None),
    ("the.office.us.s03e01e02.mkv", "episode", "The Office"),
    ("[SubsPlease] Naruto - 147 (1080p) [A1B2C3D4].mkv", "episode", "Naruto"),
])
def test_guessit_path(name, kind, title, monkeypatch):
    monkeypatch.delenv("RENNIX_PARSER", raising=False)
    p = parse_name(name)
    assert p.engine.startswith("guessit")
    assert p.kind == kind
    if title:
        names = [p.title] + [t for t, _ in p.alt_titles]
        assert any(normalize(n) == normalize(title) for n in names)
