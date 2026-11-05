"""Scanning folders, and the app bridge end to end (without a window)."""

import json
import time
from pathlib import Path

from rennix.api import Api, _version_tuple
from rennix.config import Paths, Settings
from rennix.scanner import scan
from tests.fake_tmdb import FakeTMDb


def touch(p: Path, size: int = 64):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


def test_scan_finds_videos_companions_and_junk(tmp_path):
    touch(tmp_path / "Film.2010.mkv")
    touch(tmp_path / "Film.2010.en.srt")
    touch(tmp_path / "Film.2010-poster.jpg")
    touch(tmp_path / "Film.2010.sample.mkv")
    touch(tmp_path / "Sample" / "sample.mkv")
    touch(tmp_path / "RARBG.txt")
    touch(tmp_path / "Film.2010.Trailer.mkv")
    touch(tmp_path / ".hidden.mkv")
    res = scan([tmp_path])
    names = {f.name: f for f in res.files}
    assert "Film.2010.mkv" in names
    assert {c.suffix for c in names["Film.2010.mkv"].companions} == {".en.srt", "-poster.jpg"}
    assert names["Film.2010.Trailer.mkv"].category == "extra"
    assert len(res.skipped) == 2                      # both samples
    assert [j.name for j in res.junk] == ["RARBG.txt"]
    assert ".hidden.mkv" not in names


def test_scan_reports_missing_paths(tmp_path):
    res = scan([tmp_path / "nope"])
    assert res.errors


def test_settings_survive_damage(tmp_path):
    paths = Paths(tmp_path)
    paths.settings_file.write_text("{ not json", encoding="utf-8")
    s = Settings(paths)
    assert s.get("library", "action") == "move"
    s.update({"library": {"action": "hardlink"}})
    assert Settings(paths).get("library", "action") == "hardlink"
    assert Settings(paths).get("safety", "confirm_before_rename") is True   # new options appear


class FakeWindow:
    def __init__(self):
        self.events = []

    def evaluate_js(self, js):
        start = js.index("on(") + 3
        name, payload = js[start:-1].split(", ", 1)
        self.events.append((json.loads(name), json.loads(payload)))


def wait_idle(api, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if api._busy.acquire(blocking=False):
            api._busy.release()
            return
        time.sleep(0.02)
    raise AssertionError("still busy")


def test_api_end_to_end(tmp_path):
    dl = tmp_path / "dl"
    touch(dl / "The.Matrix.1999.mkv")
    touch(dl / "Breaking.Bad.S02E05.mkv")
    touch(dl / "dune.mkv")
    api = Api(paths=Paths(tmp_path / "app"), source=FakeTMDb())
    win = FakeWindow()
    api._window = win
    assert api.add_paths([str(dl)])["started"]
    time.sleep(0.05)
    wait_idle(api)
    names = [e for e, _ in win.events]
    assert names[0] == "status" and "scanned" in names and names[-1] == "done"
    results = [p for e, p in win.events if e == "result"]
    assert {r["status"] for r in results} >= {"green", "amber"}
    green = [r for r in results if r["name"] == "The.Matrix.1999.mkv"][0]
    assert green["preview"]["name"] == "The Matrix (1999).mkv"

    # the user fixes the amber one
    amber = [r for r in results if r["name"] == "dune.mkv"][0]
    fixed = api.choose_match(amber["id"], "movie", 438631)
    assert fixed["status"] == "green" and fixed["preview"]["name"] == "Dune (2021).mkv"

    # format editor helpers
    chk = api.check_format("{title} ({year})", "movie")
    assert not [i for i in chk["issues"] if i["level"] == "error"] and chk["examples"]
    assert api.save_format("Mine", movie="{title} [{year}]")["ok"]
    assert "Mine" in api.list_formats()

    # rename everything
    assert api.rename()["started"]
    time.sleep(0.05)
    wait_idle(api)
    summary = [p for e, p in win.events if e == "renamed"][-1]
    assert summary["done"] == 3
    assert sorted(p.name for p in dl.iterdir()) == sorted(
        ["The Matrix (1999).mkv", "Dune (2021).mkv", "Breaking Bad (2008) - S02E05 - Breakage.mkv"])
    hist = api.history()
    assert hist[0]["count"] == 3
    assert api.undo(hist[0]["id"])["undone"] == 3
    assert (dl / "dune.mkv").exists()


def test_version_ordering():
    assert _version_tuple("0.1.0") > _version_tuple("0.1.0-alpha")
    assert _version_tuple("1.2.0") > _version_tuple("1.1.9")
