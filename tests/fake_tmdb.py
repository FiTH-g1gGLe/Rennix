"""A small offline stand-in for TheMovieDB, shaped like the real API answers.

Runtimes, years and season lengths are real-world values so the scoring is
tested against realistic data. Searching works like TMDb: a fuzzy title match,
optionally filtered by year.
"""

from __future__ import annotations

from rennix.textnorm import normalize, similarity
from rennix.tmdb import TMDbError

MOVIES = [
    # id, title, original title, release date, runtime, popularity, collection, imdb, alt titles
    (603, "The Matrix", "The Matrix", "1999-03-31", 136, 80.0, "The Matrix Collection", "tt0133093", []),
    (604, "The Matrix Reloaded", "The Matrix Reloaded", "2003-05-15", 138, 40.0, "The Matrix Collection", "tt0234215", []),
    (27205, "Inception", "Inception", "2010-07-15", 148, 90.0, "", "tt1375666", []),
    (438631, "Dune", "Dune", "2021-09-15", 155, 120.0, "Dune Collection", "tt1160419", []),
    (841, "Dune", "Dune", "1984-12-14", 137, 25.0, "", "tt0087182", []),
    (693134, "Dune: Part Two", "Dune: Part Two", "2024-02-27", 167, 150.0, "Dune Collection", "tt15239678", []),
    (335984, "Blade Runner 2049", "Blade Runner 2049", "2017-10-04", 164, 60.0, "Blade Runner Collection", "tt1856101", []),
    (78, "Blade Runner", "Blade Runner", "1982-06-25", 117, 50.0, "Blade Runner Collection", "tt0083658", []),
    (14161, "2012", "2012", "2009-10-10", 158, 45.0, "", "tt1190080", []),
    (530915, "1917", "1917", "2019-12-25", 119, 55.0, "", "tt8579674", []),
    (464052, "Wonder Woman 1984", "Wonder Woman 1984", "2020-12-16", 151, 70.0, "Wonder Woman Collection", "tt7126948", []),
    (297762, "Wonder Woman", "Wonder Woman", "2017-05-30", 141, 65.0, "Wonder Woman Collection", "tt0451279", []),
    (194, "Amélie", "Le Fabuleux Destin d'Amélie Poulain", "2001-04-25", 122, 30.0, "", "tt0211915", ["Amelie"]),
    (557, "Spider-Man", "Spider-Man", "2002-05-01", 121, 70.0, "Spider-Man Collection", "tt0145487", ["Spiderman"]),
    (1366, "Rocky II", "Rocky II", "1979-06-15", 119, 30.0, "Rocky Collection", "tt0079817", []),
    (1367, "Rocky III", "Rocky III", "1982-05-28", 100, 28.0, "Rocky Collection", "tt0084602", []),
    (568, "Apollo 13", "Apollo 13", "1995-06-30", 140, 35.0, "", "tt0112384", []),
    (19995, "Avatar", "Avatar", "2009-12-15", 162, 100.0, "Avatar Collection", "tt0499549", []),
    (120, "The Lord of the Rings: The Fellowship of the Ring", "The Lord of the Rings: The Fellowship of the Ring",
     "2001-12-18", 179, 95.0, "The Lord of the Rings Collection", "tt0120737", []),
    (24, "Kill Bill: Vol. 1", "Kill Bill: Vol. 1", "2003-10-10", 111, 50.0, "Kill Bill Collection", "tt0266697", []),
    (11, "Star Wars", "Star Wars", "1977-05-25", 121, 90.0, "Star Wars Collection", "tt0076759",
     ["Star Wars: Episode IV - A New Hope", "Star Wars Episode IV A New Hope"]),
    (12, "Finding Nemo", "Finding Nemo", "2003-05-30", 100, 60.0, "", "tt0266543", []),
]

SHOWS = {
    1396: dict(name="Breaking Bad", original_name="Breaking Bad", first_air_date="2008-01-20", popularity=200.0,
               origin_country=["US"], imdb="tt0903747", seasons=[(0, 9), (1, 7), (2, 13), (3, 13), (4, 13), (5, 16)],
               runtime=47, episodes={(2, 5): "Breakage", (2, 6): "Peekaboo", (1, 1): "Pilot"}),
    2316: dict(name="The Office", original_name="The Office", first_air_date="2005-03-24", popularity=150.0,
               origin_country=["US"], imdb="tt0386676", seasons=[(1, 6), (2, 22), (3, 25), (4, 19), (5, 28)],
               runtime=22, episodes={(3, 1): "Gay Witch Hunt", (3, 2): "The Convention"}),
    2996: dict(name="The Office", original_name="The Office", first_air_date="2001-07-09", popularity=40.0,
               origin_country=["GB"], imdb="tt0290978", seasons=[(0, 2), (1, 6), (2, 6)],
               runtime=30, episodes={(1, 1): "Downsize"}),
    57243: dict(name="Doctor Who", original_name="Doctor Who", first_air_date="2005-03-26", popularity=90.0,
                origin_country=["GB"], imdb="tt0436992", seasons=[(1, 13), (2, 13), (3, 13)],
                runtime=45, episodes={(1, 1): "Rose"}),
    121: dict(name="Doctor Who", original_name="Doctor Who", first_air_date="1963-11-23", popularity=30.0,
              origin_country=["GB"], imdb="tt0056751", seasons=[(1, 42), (2, 39), (3, 45)],
              runtime=25, episodes={(1, 1): "An Unearthly Child"}),
    46260: dict(name="Naruto", original_name="ナルト", first_air_date="2002-10-03", popularity=80.0,
                origin_country=["JP"], imdb="tt0409591", seasons=[(1, 220)],
                runtime=23, episodes={(1, 147): "Hunt for the Dog Team!"}),
    2224: dict(name="The Daily Show", original_name="The Daily Show", first_air_date="1996-07-22", popularity=20.0,
               origin_country=["US"], imdb="tt0115147", seasons=[(30, 120), (31, 40)],
               runtime=30, episodes={}, dated={(31, 12): "2026-03-14"}),
    1437: dict(name="Firefly", original_name="Firefly", first_air_date="2002-09-20", popularity=50.0,
               origin_country=["US"], imdb="tt0303461", seasons=[(1, 14)],
               runtime=44, episodes={(1, 1): "Serenity"}),
    615: dict(name="Futurama", original_name="Futurama", first_air_date="1999-03-28", popularity=70.0,
              origin_country=["US"], imdb="tt0149460", seasons=[(1, 9), (2, 20)],
              runtime=22, episodes={(1, 1): "Space Pilot 3000"}),
}


def _movie_result(m):
    return {"id": m[0], "title": m[1], "original_title": m[2], "release_date": m[3],
            "popularity": m[5], "poster_path": f"/p{m[0]}.jpg", "overview": ""}


class FakeTMDb:
    def __init__(self):
        self.calls: list[tuple] = []

    # searching like TMDb: matches on title words, filters by year if given
    def _match(self, query: str, title: str, alts: list[str]) -> bool:
        q = normalize(query)
        names = [title] + alts
        for n in names:
            nn = normalize(n)
            if not q or not nn:
                continue
            if q in nn or nn in q or similarity(query, n) >= 0.75:
                return True
        return False

    def search_movie(self, query, year=None):
        self.calls.append(("search_movie", query, year))
        out = []
        for m in MOVIES:
            if self._match(query, m[1], [m[2]] + m[8]) and (not year or m[3].startswith(str(year))):
                out.append(_movie_result(m))
        return sorted(out, key=lambda r: -r["popularity"])

    def search_tv(self, query, year=None):
        self.calls.append(("search_tv", query, year))
        out = []
        for sid, s in SHOWS.items():
            if self._match(query, s["name"], [s["original_name"]]) and (
                    not year or s["first_air_date"].startswith(str(year))):
                out.append({"id": sid, "name": s["name"], "original_name": s["original_name"],
                            "first_air_date": s["first_air_date"], "popularity": s["popularity"],
                            "origin_country": s["origin_country"], "poster_path": f"/t{sid}.jpg"})
        return sorted(out, key=lambda r: -r["popularity"])

    def find_by_imdb(self, imdb_id):
        self.calls.append(("find", imdb_id))
        movies = [_movie_result(m) for m in MOVIES if m[7] == imdb_id]
        tv = [{"id": sid, "name": s["name"], "first_air_date": s["first_air_date"]}
              for sid, s in SHOWS.items() if s["imdb"] == imdb_id]
        return {"movie_results": movies, "tv_results": tv, "tv_episode_results": []}

    def movie(self, movie_id):
        self.calls.append(("movie", movie_id))
        for m in MOVIES:
            if m[0] == movie_id:
                d = _movie_result(m)
                d["runtime"] = m[4]
                d["imdb_id"] = m[7]
                d["belongs_to_collection"] = {"name": m[6]} if m[6] else None
                d["alternative_titles"] = {"titles": [{"title": t} for t in m[8]]}
                d["overview"] = f"Test plot summary for {m[1]}, used by Rennix's offline tests."
                d["vote_average"] = 7.4
                d["genres"] = [{"name": "Action"}, {"name": "Science Fiction"}]
                return d
        raise TMDbError("404", "TheMovieDB has no entry for that.")

    def tv(self, tv_id):
        self.calls.append(("tv", tv_id))
        s = SHOWS.get(tv_id)
        if not s:
            raise TMDbError("404", "TheMovieDB has no entry for that.")
        seasons = [{"season_number": n, "episode_count": c,
                    "air_date": f"{int(s['first_air_date'][:4]) + max(n - 1, 0)}-01-01"} for n, c in s["seasons"]]
        if tv_id == 2224:
            seasons = [{"season_number": 30, "episode_count": 120, "air_date": "2025-01-06"},
                       {"season_number": 31, "episode_count": 40, "air_date": "2026-01-05"}]
        return {"id": tv_id, "name": s["name"], "original_name": s["original_name"],
                "first_air_date": s["first_air_date"], "popularity": s["popularity"],
                "origin_country": s["origin_country"], "poster_path": f"/t{tv_id}.jpg",
                "seasons": seasons, "external_ids": {"imdb_id": s["imdb"]},
                "alternative_titles": {"results": []}, "episode_run_time": [s["runtime"]]}

    def season(self, tv_id, season_number):
        self.calls.append(("season", tv_id, season_number))
        s = SHOWS.get(tv_id)
        if not s:
            raise TMDbError("404")
        count = dict(s["seasons"]).get(season_number)
        if count is None:
            raise TMDbError("404")
        eps = []
        for n in range(1, count + 1):
            eps.append({"episode_number": n,
                        "name": s["episodes"].get((season_number, n), f"Episode {n}"),
                        "air_date": s.get("dated", {}).get((season_number, n), ""),
                        "runtime": s["runtime"]})
        return {"season_number": season_number, "episodes": eps}


# ---------------------------------------------------------------- metadata (version 2)

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 2000 + b"\xff\xd9"
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000


def _credits():
    return {"cast": [{"name": "Keanu Reeves", "character": "Neo", "order": 0, "profile_path": "/k.jpg"},
                     {"name": "Carrie-Anne Moss", "character": "Trinity", "order": 1}],
            "crew": [{"name": "Lana Wachowski", "job": "Director"}, {"name": "Lilly Wachowski", "job": "Director"},
                     {"name": "Lana Wachowski", "job": "Screenplay"}]}


def _movie_details(self, movie_id):
    d = self.movie(movie_id)
    d.update(credits=_credits(), tagline="Welcome to the Real World.", vote_count=26000,
             production_companies=[{"name": "Warner Bros. Pictures"}], production_countries=[{"name": "United States of America"}],
             release_dates={"results": [{"iso_3166_1": "GB", "release_dates": [{"certification": "15", "type": 3}]},
                                        {"iso_3166_1": "US", "release_dates": [{"certification": "R", "type": 3}]}]},
             images={"logos": [{"file_path": "/logo_de.png", "iso_639_1": "de", "vote_average": 9},
                               {"file_path": "/logo_en.png", "iso_639_1": "en", "vote_average": 5},
                               {"file_path": "/logo.svg", "iso_639_1": "en", "vote_average": 10}]},
             videos={"results": [{"site": "YouTube", "type": "Trailer", "key": "abc123", "official": True}]},
             external_ids={"imdb_id": d.get("imdb_id")})
    return d


def _tv_details(self, tv_id):
    d = self.tv(tv_id)
    for s in d["seasons"]:
        s["poster_path"] = f"/s{tv_id}_{s['season_number']}.jpg"
    d.update(overview="A chemistry teacher turns to crime.", genres=[{"name": "Drama"}], networks=[{"name": "AMC"}],
             vote_average=8.9, vote_count=15000, status="Ended", credits=_credits(),
             content_ratings={"results": [{"iso_3166_1": "GB", "rating": "18"}]},
             images={"logos": [{"file_path": f"/tlogo{tv_id}.png", "iso_639_1": "en", "vote_average": 5}]},
             external_ids={"imdb_id": d["external_ids"]["imdb_id"], "tvdb_id": 81189})
    return d


_plain_season = FakeTMDb.season


def _season(self, tv_id, season_number):
    d = _plain_season(self, tv_id, season_number)
    d["poster_path"] = f"/s{tv_id}_{season_number}.jpg"
    for e in d["episodes"]:
        e.update(id=tv_id * 1000 + e["episode_number"], season_number=season_number,
                 overview=f"Plot of {e['name']}.", still_path=f"/e{tv_id}_{season_number}_{e['episode_number']}.jpg",
                 vote_average=8.0, crew=[{"name": "Vince Gilligan", "job": "Director"}],
                 guest_stars=[{"name": "Guest Star", "character": "Someone", "order": 0}])
    return d


def _download(self, url, dest):
    self.calls.append(("download", url))
    if getattr(self, "fail_downloads", False):
        raise TMDbError("down", code="RNX-M009")
    with open(dest, "wb") as fh:
        fh.write(FAKE_PNG if url.endswith(".png") else FAKE_JPEG)


FakeTMDb.movie_details = _movie_details
FakeTMDb.tv_details = _tv_details
FakeTMDb.season = _season
FakeTMDb.download = _download
