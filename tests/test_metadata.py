"""Version 2: posters, logos, .nfo files and tags, end to end and offline."""

import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from rennix import metadata
from rennix.api import Api
from rennix.config import Paths
from tests.fake_tmdb import FakeTMDb
from tests.test_scanner_and_api import FakeWindow, touch, wait_idle


def files_under(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())


# ---------------------------------------------------------------- details and .nfo

def test_movie_details_are_picked_sensibly():
    fake = FakeTMDb()
    m = metadata.movie_info(fake.movie_details(603), region="GB", language="en-GB")
    assert m["certification"] == "15"                         # UK rating first
    assert metadata.movie_info(fake.movie_details(603), region="FR")["certification"] == "R"  # falls back to US
    assert m["logo"].endswith("/logo_en.png")                  # English, and never an .svg
    assert m["directors"] == ["Lana Wachowski", "Lilly Wachowski"]
    assert m["writers"] == ["Lana Wachowski"]
    assert m["trailer"].endswith("videoid=abc123")
    assert m["cast"][0] == {"name": "Keanu Reeves", "role": "Neo", "order": 0,
                            "thumb": "https://image.tmdb.org/t/p/original/k.jpg"}


def test_movie_nfo_is_valid_kodi_xml():
    m = metadata.movie_info(FakeTMDb().movie_details(603))
    root = ET.fromstring(metadata.movie_nfo(m).split("-->", 1)[1])
    assert root.tag == "movie"
    assert root.findtext("title") == "The Matrix"
    assert root.findtext("year") == "1999"
    assert root.find("uniqueid[@type='tmdb']").text == "603"
    assert root.find("uniqueid[@type='imdb']").text == "tt0133093"
    assert [a.findtext("name") for a in root.findall("actor")] == ["Keanu Reeves", "Carrie-Anne Moss"]
    assert root.find("set/name").text == "The Matrix Collection"


def test_double_episode_nfo_has_two_entries():
    fake = FakeTMDb()
    show = metadata.show_info(fake.tv_details(1396))
    season = fake.season(1396, 2)
    eps = [metadata.episode_info(season, 5), metadata.episode_info(season, 6)]
    text = metadata.episode_nfo(show, eps)
    assert text.count("<episodedetails>") == 2 and text.count("<?xml") == 1
    tags = metadata.episode_tags(show, eps)
    assert tags["title"] == "Breakage & Peekaboo" and tags["show"] == "Breaking Bad" and tags["episode"] == 5


@pytest.mark.parametrize("server,own,poster,logo", [
    ("plex", True, "poster.jpg", "logo.png"),
    ("plex", False, "The Matrix (1999).jpg", None),
    ("kodi", True, "poster.jpg", "clearlogo.png"),
    ("kodi", False, "The Matrix (1999)-poster.jpg", "The Matrix (1999)-clearlogo.png"),
    ("jellyfin", False, "The Matrix (1999)-poster.jpg", "The Matrix (1999)-logo.png"),
])
def test_artwork_names_follow_the_media_server(server, own, poster, logo):
    info = {"poster": "https://x/p.jpg", "logo": "https://x/l.png"}
    t = metadata.movie_targets(Path("/m/The Matrix (1999)/The Matrix (1999).mkv"), server, own, info)
    assert t["poster"][0].name == poster
    assert (t["logo"][0].name if "logo" in t else None) == logo
    assert t["nfo"][0].name == "The Matrix (1999).nfo"


def test_season_poster_names():
    show, season = Path("/tv/Breaking Bad (2008)"), Path("/tv/Breaking Bad (2008)/Season 02")
    assert metadata.season_poster_target("plex", show, season, 2, "u.jpg").name == "Season02.jpg"
    assert metadata.season_poster_target("plex", show, season, 0, "u.jpg").name == "season-specials-poster.jpg"
    assert metadata.season_poster_target("kodi", show, season, 2, "u.jpg") == show / "season02-poster.jpg"
    assert metadata.season_poster_target("plex", show, None, 2, "u.jpg") is None


def test_presets():
    assert metadata.preset_for("plex") == {"poster": True, "logo": False, "nfo": False, "season_posters": False,
                                           "episode_thumbs": False, "tags": False, "cover": False}
    assert all(metadata.preset_for("jellyfin")[k] for k in ("poster", "logo", "nfo", "season_posters", "episode_thumbs"))
    assert not metadata.preset_for("kodi")["tags"]          # inside the file is never pre-ticked


# ---------------------------------------------------------------- through the app

def make_api(tmp_path, server="jellyfin", **md):
    api = Api(paths=Paths(tmp_path / "app"), source=FakeTMDb())
    api._window = FakeWindow()
    api.save_settings({"general": {"media_server": server},
                       "library": {"movies_dir": str(tmp_path / "Movies"), "tv_dir": str(tmp_path / "TV"),
                                   "sort_into_library": True},
                       "naming": {"movie_format": "Jellyfin" if server != "plex" else "Plex",
                                  "episode_format": "Jellyfin" if server != "plex" else "Plex"}})
    api.save_settings({"metadata": {"enabled": True, **md}})
    return api


def run(api, folder):
    api.add_paths([str(folder)])
    time.sleep(0.05)
    wait_idle(api)
    api.rename()
    time.sleep(0.05)
    wait_idle(api)
    return [p for e, p in api._window.events if e == "renamed"][-1]


def test_first_switch_on_uses_the_media_server_preset(tmp_path):
    api = make_api(tmp_path, server="plex")
    md = api._settings.data["metadata"]
    assert md["configured"] and md["poster"] and not md["nfo"] and not md["tags"]
    api.save_settings({"metadata": {"nfo": True}})
    api.save_settings({"metadata": {"enabled": False}})
    api.save_settings({"metadata": {"enabled": True}})
    assert api._settings.data["metadata"]["nfo"] is True      # the user's choices stick after that


def test_everything_next_to_the_video_then_undo(tmp_path):
    dl = tmp_path / "dl"
    touch(dl / "The.Matrix.1999.mkv")
    touch(dl / "Breaking.Bad.S02E05.mkv")
    api = make_api(tmp_path, server="jellyfin")
    summary = run(api, dl)
    assert summary["done"] == 2 and summary["meta"] == 2 and not summary["errors"]
    assert files_under(tmp_path / "Movies") == [
        "The Matrix (1999) [tmdbid-603]/The Matrix (1999).mkv",
        "The Matrix (1999) [tmdbid-603]/The Matrix (1999).nfo",
        "The Matrix (1999) [tmdbid-603]/logo.png",
        "The Matrix (1999) [tmdbid-603]/poster.jpg",
    ]
    tv = files_under(tmp_path / "TV")
    show = "Breaking Bad (2008) [tmdbid-1396]"
    ep = "Breaking Bad S02E05 Breakage"
    assert f"{show}/poster.jpg" in tv and f"{show}/logo.png" in tv and f"{show}/tvshow.nfo" in tv
    assert f"{show}/season02-poster.jpg" in tv
    ep_files = [t for t in tv if "/Season 02/" in t]
    assert any(t.endswith("-thumb.jpg") for t in ep_files) and any(t.endswith(".nfo") for t in ep_files)

    hist = api.history()[0]
    assert hist["count"] == 2 and hist["meta"] == 2 and hist["can_undo"]
    r = api.undo(hist["id"])
    assert not r["errors"]
    assert files_under(tmp_path) and not (tmp_path / "Movies").exists() and not (tmp_path / "TV").exists()
    assert sorted(p.name for p in dl.iterdir()) == ["Breaking.Bad.S02E05.mkv", "The.Matrix.1999.mkv"]
    assert not any((tmp_path / "app").rglob("backups/*/*"))   # backups tidied once nothing is left to undo


def test_plex_gets_just_the_poster(tmp_path):
    dl = tmp_path / "dl"
    touch(dl / "The.Matrix.1999.mkv")
    run(make_api(tmp_path, server="plex"), dl)
    assert files_under(tmp_path / "Movies") == ["The Matrix (1999) {tmdb-603}/The Matrix (1999).mkv",
                                               "The Matrix (1999) {tmdb-603}/poster.jpg"]


def test_already_named_files_get_metadata_without_a_rename(tmp_path):
    lib = tmp_path / "Movies" / "The Matrix (1999) [tmdbid-603]"
    touch(lib / "The Matrix (1999).mkv")
    api = make_api(tmp_path, server="jellyfin")
    summary = run(api, tmp_path / "Movies")
    assert summary["done"] == 0 and summary["meta"] == 1
    assert (lib / "poster.jpg").exists() and (lib / "The Matrix (1999).nfo").exists()
    assert api.history()[0]["count"] == 0 and api.history()[0]["meta"] == 1


def test_existing_poster_is_kept_unless_replace_is_on(tmp_path):
    lib = tmp_path / "Movies" / "The Matrix (1999) [tmdbid-603]"
    touch(lib / "The Matrix (1999).mkv")
    (lib / "poster.jpg").write_bytes(b"mine")
    api = make_api(tmp_path, server="jellyfin")
    run(api, tmp_path / "Movies")
    assert (lib / "poster.jpg").read_bytes() == b"mine"

    api2 = make_api(tmp_path / "second", server="jellyfin", replace_existing=True)
    api2._settings.update({"library": {"movies_dir": str(tmp_path / "Movies")}})
    run(api2, tmp_path / "Movies")
    assert (lib / "poster.jpg").read_bytes() != b"mine"
    api2.undo(api2.history()[0]["id"])
    assert (lib / "poster.jpg").read_bytes() == b"mine"      # undo brings the user's own poster back


def test_downloads_failing_still_renames(tmp_path):
    dl = tmp_path / "dl"
    touch(dl / "The.Matrix.1999.mkv")
    api = make_api(tmp_path, server="plex")
    api._source.fail_downloads = True
    summary = run(api, dl)
    assert summary["done"] == 1
    assert files_under(tmp_path / "Movies") == ["The Matrix (1999) {tmdb-603}/The Matrix (1999).mkv"]


def test_metadata_off_changes_nothing(tmp_path):
    dl = tmp_path / "dl"
    touch(dl / "The.Matrix.1999.mkv")
    api = make_api(tmp_path, server="jellyfin")
    api.save_settings({"metadata": {"enabled": False}})
    run(api, dl)
    assert files_under(tmp_path / "Movies") == ["The Matrix (1999) [tmdbid-603]/The Matrix (1999).mkv"]
    assert not [c for c in api._source.calls if c[0] == "download"]


def test_tags_are_skipped_for_hardlinks_and_other_formats(tmp_path):
    dl = tmp_path / "dl"
    touch(dl / "The.Matrix.1999.avi")
    api = make_api(tmp_path, server="plex", tags=True)
    run(api, dl)
    problems = [p for e, p in api._window.events if e == "renamed"][-1]["problems"]
    assert any("only go into MKV and MP4" in p["message"] for p in problems)


def test_undo_then_rename_stale_list_saves_nothing(tmp_path):
    """The bug from the 0.2.1 report: undo moved the films away, then the old list was renamed."""
    dl = tmp_path / "dl"
    touch(dl / "The.Matrix.1999.mkv")
    api = make_api(tmp_path, server="jellyfin")
    run(api, dl)
    lib = tmp_path / "Movies" / "The Matrix (1999) [tmdbid-603]"
    api.add_paths([str(tmp_path / "Movies")])         # the renamed film is loaded again
    time.sleep(0.05)
    wait_idle(api)
    assert api._order
    r = api.undo(api.history()[0]["id"])              # ...then the first batch is undone
    assert r["removed_ids"] and not api._order        # the stale entry leaves the list
    assert not lib.exists()


def test_metadata_never_lands_in_a_folder_whose_film_has_gone(tmp_path):
    lib = tmp_path / "Movies" / "The Matrix (1999) [tmdbid-603]"
    touch(lib / "The Matrix (1999).mkv")
    api = make_api(tmp_path, server="jellyfin", tags=True)
    api.add_paths([str(tmp_path / "Movies")])
    time.sleep(0.05)
    wait_idle(api)
    (lib / "The Matrix (1999).mkv").rename(tmp_path / "elsewhere.mkv")   # moved by hand afterwards
    api.rename()
    time.sleep(0.05)
    wait_idle(api)
    assert not [p for p in lib.iterdir()]                                # no orphan poster or .nfo
    removed = [p for e, p in api._window.events if e == "removed"]
    assert removed and "moved or gone" in removed[-1]["message"]
