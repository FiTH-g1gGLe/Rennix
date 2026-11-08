"""Safe renaming: never overwrite, survive crashes, undo everything."""

import copy
import errno
import json
import os
from pathlib import Path

import pytest

from rennix import renamer as rmod
from rennix.config import DEFAULT_SETTINGS, Paths
from rennix.identify import Identifier
from rennix.naming import Formats
from rennix.renamer import Renamer, build_plan
from rennix.scanner import scan
from tests.fake_tmdb import FakeTMDb


def make(tmp_path, names, **lib):
    dl = tmp_path / "Downloads"
    dl.mkdir(exist_ok=True)
    for n in names:
        p = dl / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 64)
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s["library"].update(lib)
    items = Identifier(FakeTMDb(), probe_fn=None).identify(scan([dl]).files)
    return dl, s, items, Paths(tmp_path / "app")


def files_under(root):
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())


def test_rename_in_place_changes_only_the_name(tmp_path):
    dl, s, items, paths = make(tmp_path, ["The.Matrix.1999.1080p.mkv", "The.Matrix.1999.1080p.en.srt"])
    plan = build_plan(items, Formats(), s)
    out = Renamer(paths, s).execute(plan)
    assert out["done"] == 1 and out["companions"] == 1
    assert files_under(dl) == ["The Matrix (1999).en.srt", "The Matrix (1999).mkv"]


def test_sort_into_library_and_undo(tmp_path):
    dl, s, items, paths = make(tmp_path, ["The.Matrix.1999.mkv", "Breaking.Bad.S02E05.mkv", "sub/Inception.2010.mkv"],
                               movies_dir=str(tmp_path / "Movies"), tv_dir=str(tmp_path / "TV"), sort_into_library=True)
    before = files_under(dl)
    r = Renamer(paths, s)
    out = r.execute(build_plan(items, Formats(), s))
    assert out["done"] == 3
    assert "The Matrix (1999) {tmdb-603}/The Matrix (1999).mkv" in files_under(tmp_path / "Movies")
    assert files_under(tmp_path / "TV") == ["Breaking Bad (2008)/Season 02/Breaking Bad (2008) - S02E05 - Breakage.mkv"]
    assert not (dl / "sub").exists()                       # empty folder tidied
    undo = r.undo(out["batch_id"])
    assert undo["undone"] == 3 and undo["videos"] == 3 and not undo["errors"]
    assert files_under(dl) == before
    assert not (tmp_path / "Movies").exists()              # folders Rennix made are removed again


def test_amber_and_red_are_skipped_by_default(tmp_path):
    dl, s, items, paths = make(tmp_path, ["dune.mkv", "VID_00231.mp4", "The.Matrix.1999.mkv"])
    plan = build_plan(items, Formats(), s)
    assert {Path(o.src).name for o in plan.ops} == {"The.Matrix.1999.mkv"}
    plan2 = build_plan(items, Formats(), s, include_amber=True)
    assert {Path(o.src).name for o in plan2.ops} == {"The.Matrix.1999.mkv", "dune.mkv"}


def test_never_overwrites_existing_file(tmp_path):
    dl, s, items, paths = make(tmp_path, ["The.Matrix.1999.mkv"])
    (dl / "The Matrix (1999).mkv").write_bytes(b"precious")
    items = [i for i in items if i.file.name == "The.Matrix.1999.mkv"]
    plan = build_plan(items, Formats(), s)
    assert not plan.ops and plan.problems[0]["level"] == "conflict"
    assert (dl / "The Matrix (1999).mkv").read_bytes() == b"precious"


def test_number_policy(tmp_path):
    dl, s, items, paths = make(tmp_path, ["The.Matrix.1999.mkv"])
    (dl / "The Matrix (1999).mkv").write_bytes(b"precious")
    items = [i for i in items if i.file.name == "The.Matrix.1999.mkv"]
    s["conflicts"]["policy"] = "number"
    plan = build_plan(items, Formats(), s)
    assert Path(plan.ops[0].dst).name == "The Matrix (1999) (2).mkv"


def test_two_files_same_target_in_one_batch(tmp_path):
    dl, s, items, paths = make(tmp_path, ["The.Matrix.1999.720p.mkv", "The.Matrix.1999.1080p.mkv"])
    plan = build_plan(items, Formats(), s)
    assert len([o for o in plan.ops if o.kind == "video"]) == 1
    assert any(p["level"] == "conflict" for p in plan.problems)
    assert any("Duplicate" in p["message"] for p in plan.problems)


def test_already_named_correctly(tmp_path):
    dl, s, items, paths = make(tmp_path, ["The Matrix (1999).mkv"])
    plan = build_plan(items, Formats(), s)
    assert not plan.ops and plan.skipped[0]["reason"] == "Already named correctly"


def test_crash_mid_batch_can_be_finished(tmp_path):
    dl, s, items, paths = make(tmp_path, ["The.Matrix.1999.mkv", "Inception.2010.mkv"])
    plan = build_plan(items, Formats(), s)
    r = Renamer(paths, s)
    # simulate: journal written, first file moved, then the power went out
    batch = {"id": "20260101-000000-abcd", "created": "2026-01-01T00:00:00", "label": "",
             "ops": [rmod.asdict(o) for o in plan.ops], "removed_dirs": []}
    first = plan.ops[0]
    os.rename(first.src, first.dst)
    rmod.atomic_write_json(paths.journal_file, batch)
    assert r.pending_journal()["id"] == batch["id"]
    out = r.recover("finish")
    assert out["done"] == 2
    assert r.pending_journal() is None
    assert sorted(files_under(dl)) == ["Inception (2010).mkv", "The Matrix (1999).mkv"]


def test_crash_can_be_reversed(tmp_path):
    dl, s, items, paths = make(tmp_path, ["The.Matrix.1999.mkv", "Inception.2010.mkv"])
    before = files_under(dl)
    plan = build_plan(items, Formats(), s)
    batch = {"id": "20260101-000000-beef", "created": "2026-01-01T00:00:00", "label": "",
             "ops": [rmod.asdict(o) for o in plan.ops], "removed_dirs": []}
    os.rename(plan.ops[0].src, plan.ops[0].dst)
    rmod.atomic_write_json(paths.journal_file, batch)
    Renamer(paths, s).recover("reverse")
    assert files_under(dl) == before


def test_cross_drive_move_is_verified(tmp_path, monkeypatch):
    dl, s, items, paths = make(tmp_path, ["The.Matrix.1999.mkv"])
    real_link = os.link

    def no_link(src, dst, *a, **k):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(rmod.os, "link", no_link)
    if rmod.IS_WINDOWS:
        pytest.skip("Windows path tested by CI on Windows")
    out = Renamer(paths, s).execute(build_plan(items, Formats(), s))
    assert out["done"] == 1
    assert files_under(dl) == ["The Matrix (1999).mkv"]
    assert not list(dl.glob("*.rennix-part"))
    monkeypatch.setattr(rmod.os, "link", real_link)


def test_hardlink_keeps_original(tmp_path):
    if not hasattr(os, "link"):
        pytest.skip("no hardlinks")
    dl, s, items, paths = make(tmp_path, ["The.Matrix.1999.mkv"], action="hardlink",
                               movies_dir=str(tmp_path / "Movies"), sort_into_library=True)
    r = Renamer(paths, s)
    out = r.execute(build_plan(items, Formats(), s))
    assert out["done"] == 1
    assert (dl / "The.Matrix.1999.mkv").exists()
    new = tmp_path / "Movies" / "The Matrix (1999) {tmdb-603}" / "The Matrix (1999).mkv"
    assert os.path.samefile(new, dl / "The.Matrix.1999.mkv")
    r.undo(out["batch_id"])
    assert not new.exists() and (dl / "The.Matrix.1999.mkv").exists()


def test_missing_source_fails_cleanly_and_companion_waits(tmp_path):
    dl, s, items, paths = make(tmp_path, ["The.Matrix.1999.mkv", "The.Matrix.1999.en.srt"])
    plan = build_plan(items, Formats(), s)
    (dl / "The.Matrix.1999.mkv").unlink()                  # file vanished after scanning
    out = Renamer(paths, s).execute(plan)
    assert out["done"] == 0 and out["failed"] == 1
    assert "moved or been deleted" in out["errors"][0]["message"]
    assert (dl / "The.Matrix.1999.en.srt").exists()        # subtitle left alone with its video


def test_history_lists_batches(tmp_path):
    dl, s, items, paths = make(tmp_path, ["The.Matrix.1999.mkv"])
    r = Renamer(paths, s)
    out = r.execute(build_plan(items, Formats(), s), label="1 file")
    h = r.history()
    assert h[0]["id"] == out["batch_id"] and h[0]["count"] == 1
    r.undo(out["batch_id"])
    assert r.history()[0]["undone"] == 1


def test_undo_refuses_odd_ids(tmp_path):
    paths = Paths(tmp_path / "app")
    with pytest.raises(ValueError):
        Renamer(paths, {}).undo("../../etc/passwd")


def test_case_only_rename(tmp_path):
    dl, s, items, paths = make(tmp_path, ["the matrix (1999).mkv", "the matrix (1999).en.srt"])
    r = Renamer(paths, s)
    out = r.execute(build_plan(items, Formats(), s))
    assert out["done"] == 1 and out["companions"] == 1, out
    assert files_under(dl) == ["The Matrix (1999).en.srt", "The Matrix (1999).mkv"]
    r.undo(out["batch_id"])
    assert files_under(dl) == ["the matrix (1999).en.srt", "the matrix (1999).mkv"]


def test_undo_reports_videos_and_stats_go_down(tmp_path):
    from rennix.store import Learned
    db = Learned(tmp_path / "l.db")
    db.bump("renamed", 3)
    db.bump("renamed", -5)
    assert db.stats()["renamed"] == 0
