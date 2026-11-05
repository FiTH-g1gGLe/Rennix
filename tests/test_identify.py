"""The identification engine, against an offline copy of TheMovieDB."""

from pathlib import Path

import pytest

from rennix.identify import Identifier, map_absolute
from rennix.scanner import MediaFile
from rennix.store import Learned
from tests.fake_tmdb import FakeTMDb

RUNTIMES = {"dune.mkv": 155, "Dune.1984.mkv": 137, "dune.long.mkv": 200}


def probe(path):
    m = RUNTIMES.get(Path(path).name)
    return {"minutes": m} if m else {}


def identify(*names, learned=None, strictness="normal", mode="auto", folder="/media/dl"):
    files = [MediaFile(id=f"f{i}", path=Path(folder) / n, size=1000 + i) for i, n in enumerate(names)]
    ident = Identifier(FakeTMDb(), learned=learned, probe_fn=probe, max_workers=4,
                       strictness=strictness, mode=mode)
    return {r.file.name: r for r in ident.identify(files)}


GREEN_MOVIES = [
    ("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv", "The Matrix", 1999),
    ("inception_2010_720p.mp4", "Inception", 2010),
    ("Dune.1984.mkv", "Dune", 1984),
    ("Blade.Runner.2049.2017.2160p.mkv", "Blade Runner 2049", 2017),
    ("1917 (2019).mkv", "1917", 2019),
    ("Wonder.Woman.1984.2020.1080p.mkv", "Wonder Woman 1984", 2020),
    ("Avatar.2009.Extended.Directors.Cut.1080p.mkv", "Avatar", 2009),
    ("Kill Bill Vol 1 (2003).mkv", "Kill Bill: Vol. 1", 2003),
    ("Spiderman.2002.mkv", "Spider-Man", 2002),
    ("Amelie (2001).mkv", "Amélie", 2001),
    ("Rocky.II.1979.mkv", "Rocky II", 1979),
    ("Star.Wars.Episode.IV.A.New.Hope.1977.mkv", "Star Wars", 1977),
    ("Apollo 13 (1995).mkv", "Apollo 13", 1995),
    ("The Lord of the Rings The Fellowship of the Ring 2001 Extended CD1.avi",
     "The Lord of the Rings: The Fellowship of the Ring", 2001),
]


@pytest.mark.parametrize("name,title,year", GREEN_MOVIES)
def test_confident_movies(name, title, year):
    r = identify(name)[name]
    assert r.status == "green", r.reasons
    assert (r.match.title, r.match.year) == (title, year)


def test_ids_in_name_are_certain():
    r = identify("Amelie (2001) {tmdb-194}.mkv", "tt0133093.mkv")
    assert r["Amelie (2001) {tmdb-194}.mkv"].match.tmdb_id == 194
    assert r["tt0133093.mkv"].match.title == "The Matrix"
    assert all(x.status == "green" for x in r.values())


def test_remake_without_year_is_amber_but_runtime_picks_the_right_one():
    r = identify("dune.mkv")["dune.mkv"]
    assert r.status == "amber"
    assert (r.match.title, r.match.year) == ("Dune", 2021)
    assert r.confidence < 0.8   # shown honestly, not as 100%
    assert any("Runtime matches" in x for x in r.reasons)


def test_title_only_is_never_green():
    r = identify("2012.mkv")["2012.mkv"]
    assert r.status == "amber"
    assert r.match.title == "2012"


def test_number_that_is_part_of_title():
    r = identify("Wonder.Woman.1984.mkv")["Wonder.Woman.1984.mkv"]
    assert r.status in ("amber", "green")
    assert r.match.title == "Wonder Woman 1984"


def test_generic_name_is_red():
    r = identify("VID_00231.mp4")["VID_00231.mp4"]
    assert r.status == "red" and r.match is None


@pytest.mark.parametrize("name,show,year,season,episodes", [
    ("Breaking.Bad.S02E05.Breakage.720p.HDTV.x264-CTU.mkv", "Breaking Bad", 2008, 2, [5]),
    ("breaking bad s02e06 720p.mkv", "Breaking Bad", 2008, 2, [6]),
    ("the.office.us.s03e01e02.mkv", "The Office", 2005, 3, [1, 2]),
    ("The.Office.UK.S01E01.mkv", "The Office", 2001, 1, [1]),
    ("doctor who 2005 s01e01 rose.mkv", "Doctor Who", 2005, 1, [1]),
    ("[SubsPlease] Naruto - 147 (1080p) [A1B2C3D4].mkv", "Naruto", 2002, 1, [147]),
    ("The.Daily.Show.2026.03.14.Guest.720p.mkv", "The Daily Show", 1996, 31, [12]),
    ("Futurama.1x01.Space.Pilot.3000.mkv", "Futurama", 1999, 1, [1]),
])
def test_confident_episodes(name, show, year, season, episodes):
    r = identify(name)[name]
    assert r.status == "green", r.reasons
    m = r.match
    assert (m.title, m.year, m.season, m.episodes) == (show, year, season, episodes)


def test_episode_titles_fetched():
    r = identify("the.office.us.s03e01e02.mkv")["the.office.us.s03e01e02.mkv"]
    assert r.match.episode_titles == ["Gay Witch Hunt", "The Convention"]


def test_remake_show_without_year_is_amber():
    r = identify("doctor.who.s01e01.mkv")["doctor.who.s01e01.mkv"]
    assert r.status == "amber"
    assert any("Close call" in x for x in r.reasons)


def test_episode_that_does_not_exist_is_red():
    r = identify("Breaking.Bad.S09E20.mkv")["Breaking.Bad.S09E20.mkv"]
    assert r.status == "red"
    assert r.match is None


def test_group_decides_once():
    names = [f"Breaking.Bad.S02E{e:02d}.mkv" for e in range(1, 8)]
    res = identify(*names)
    assert {r.match.tmdb_id for r in res.values()} == {1396}
    assert all(r.status == "green" for r in res.values())


def test_season_folder_layout():
    files = [MediaFile(id="a", path=Path("/tv/Firefly/Season 1/Firefly - 01 - Serenity.mkv"), size=1)]
    r = Identifier(FakeTMDb(), probe_fn=None).identify(files)[0]
    assert r.status == "green" and r.match.title == "Firefly" and r.match.episodes == [1]


def test_movie_mode_forces_films():
    r = identify("Show.Name.S01E01.mkv", mode="movie")["Show.Name.S01E01.mkv"]
    assert r.parsed.kind == "movie"


def test_strict_mode_makes_fewer_greens():
    relaxed = identify("doctor who 2005 s01e01 rose.mkv", strictness="relaxed")
    assert relaxed["doctor who 2005 s01e01 rose.mkv"].status == "green"


def test_learned_choice_is_used_next_time(tmp_path):
    learned = Learned(tmp_path / "l.db")
    first = identify("dune.mkv", learned=learned)["dune.mkv"]
    ident = Identifier(FakeTMDb(), learned=learned, probe_fn=probe)
    ident.apply_choice(first, "movie", 841)               # the user picks the 1984 film
    again = identify("dune.mkv", learned=learned)["dune.mkv"]
    assert again.status == "green"
    assert (again.match.title, again.match.year) == ("Dune", 1984)
    assert any("before" in r for r in again.reasons)


def test_manual_search():
    ident = Identifier(FakeTMDb(), probe_fn=None)
    res = ident.search("movie", "Dune")
    assert {c.year for c in res} >= {1984, 2021}
    assert ident.search("tv", "office")[0].title == "The Office"


def test_map_absolute():
    seasons = [{"season_number": 0, "episode_count": 5}, {"season_number": 1, "episode_count": 10},
               {"season_number": 2, "episode_count": 12}]
    assert map_absolute(seasons, 3) == (1, 3)
    assert map_absolute(seasons, 12) == (2, 2)
    assert map_absolute(seasons, 30) is None


def test_reasons_explain_every_green():
    res = identify(*[n for n, _, _ in GREEN_MOVIES])
    for r in res.values():
        assert r.reasons, r.file.name


def test_one_failure_does_not_stop_the_batch():
    class Broken(FakeTMDb):
        def search_movie(self, query, year=None):
            if "Matrix" in query:
                raise RuntimeError("boom")
            return super().search_movie(query, year)

    files = [MediaFile(id="a", path=Path("The.Matrix.1999.mkv"), size=1),
             MediaFile(id="b", path=Path("Inception.2010.mkv"), size=2)]
    out = {r.file.id: r for r in Identifier(Broken(), probe_fn=None).identify(files)}
    assert out["a"].status == "red"
    assert out["b"].status == "green"
