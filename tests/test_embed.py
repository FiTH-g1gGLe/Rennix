"""Writing tags inside video files: the safety net, with real MKV and MP4 files.

Real files are made with ffmpeg (skipped if it isn't installed). The tests that
need mkvpropedit or mutagen are skipped where those aren't installed; GitHub
runs them on Windows, Mac and Linux before every release.
"""

import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rennix import ebml, embed, mp4box

FFMPEG = shutil.which("ffmpeg")
MKVPROPEDIT = embed.find_mkvpropedit()
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
needs_mkvtool = pytest.mark.skipif(not (FFMPEG and MKVPROPEDIT), reason="mkvpropedit not installed")
needs_mutagen = pytest.mark.skipif(not (FFMPEG and embed.have_mutagen()), reason="mutagen not installed")

TAGS = {"kind": "movie", "title": "The Matrix", "date": "1999-03-31", "genres": ["Action", "Science Fiction"],
        "plot": "A hacker learns the truth — with “quotes” & <angle> brackets.", "directors": ["Lana Wachowski"],
        "writers": ["Lilly Wachowski"], "cast": ["Keanu Reeves", "Carrie-Anne Moss"], "certification": "15",
        "imdb": "tt0133093", "tmdb": "movie/603", "studio": "Warner Bros."}


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def ff(*args):
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """MKV with two audio tracks, subtitles and chapters; MP4 with the index at the end and at the front."""
    if not FFMPEG:
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("media")
    srt = d / "subs.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n\n2\n00:00:04,000 --> 00:00:06,000\nWorld\n", encoding="utf-8")
    chapters = d / "chapters.txt"
    chapters.write_text(";FFMETADATA1\ntitle=Old title\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=5000\ntitle=One\n"
                        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=5000\nEND=12000\ntitle=Two\n", encoding="utf-8")
    src = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-f", "lavfi", "-i", "sine=frequency=440",
           "-f", "lavfi", "-i", "sine=frequency=660"]
    ff(*src, "-i", str(srt), "-i", str(chapters), "-t", "12", "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
       "-map_metadata", "4", "-map_chapters", "4", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
       "-c:s", "srt", str(d / "film.mkv"))
    ff(*src[:8], "-t", "12", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(d / "end.mp4"))
    ff("-i", str(d / "end.mp4"), "-c", "copy", "-movflags", "+faststart", str(d / "front.mp4"))
    return d


@pytest.fixture
def copy_of(media, tmp_path):
    def make(name):
        dst = tmp_path / ("work" + Path(name).suffix)
        shutil.copy2(media / name, dst)
        return dst
    return make


# ---------------------------------------------------------------- structure readers

@needs_ffmpeg
def test_readers_understand_real_files(media):
    m = ebml.read(media / "film.mkv")
    assert m.clusters > 0 and 0 < m.first_cluster < m.clusters_end <= m.size
    assert m.duration and m.tracks_hash and m.title == "Old title"
    end, front = mp4box.read(media / "end.mp4"), mp4box.read(media / "front.mp4")
    assert not end.mdat_after_moov and front.mdat_after_moov
    assert len(end.track_hashes) == len(front.track_hashes) == 2
    assert abs(end.duration[0] / end.duration[1] - front.duration[0] / front.duration[1]) < 0.5


def test_readers_refuse_rubbish(tmp_path):
    bad = tmp_path / "bad.mkv"
    bad.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 50)
    with pytest.raises(ebml.MkvError):
        ebml.read(bad)
    with pytest.raises(embed.EmbedError) as e:
        embed.write(bad, TAGS, None, tmp_path / "b", "x", _writer=lambda p, k: None)
    assert e.value.code == "RNX-M002"
    txt = tmp_path / "x.mkv"
    txt.write_text("not a video")
    with pytest.raises(embed.EmbedError):
        embed.write(txt, TAGS, None, tmp_path / "b", "x", _writer=lambda p, k: None)


@needs_ffmpeg
def test_cut_short_file_is_refused(media, tmp_path):
    cut = tmp_path / "cut.mkv"
    data = (media / "film.mkv").read_bytes()
    cut.write_bytes(data[: len(data) // 2])        # like a half-finished download
    with pytest.raises(embed.EmbedError) as e:
        embed.write(cut, TAGS, None, tmp_path / "b", "x", _writer=lambda p, k: None)
    assert e.value.code == "RNX-M002"
    assert sha(cut) == hashlib.sha256(data[: len(data) // 2]).hexdigest()


# ---------------------------------------------------------------- the safety net (stand-in writers)

def header_scribbler(path, kind):
    """Pretends to be a tag writer that wrecks the header: must be caught and fully undone."""
    with open(path, "r+b") as f:
        f.seek(40)
        f.write(b"\xff" * 64)


def tail_appender(path, kind):
    """Pretends to be mkvpropedit moving tags to the end of the file (valid, harmless)."""
    with open(path, "ab") as f:
        f.write(b"\xec\x88" + b"\x00" * 8)          # an EBML Void element after the segment


def video_damager(path, kind):
    """Pretends to be a tool that damages the video data itself."""
    s = ebml.read(path)
    with open(path, "r+b") as f:
        f.seek((s.first_cluster + s.clusters_end) // 2)
        f.write(b"\x00" * 4096)


@needs_ffmpeg
def test_broken_header_is_put_back_exactly(copy_of, tmp_path):
    f = copy_of("film.mkv")
    before, mtime = sha(f), os.path.getmtime(f)
    with pytest.raises(embed.EmbedError):
        embed.write(f, None, None, tmp_path / "b", "o1", _writer=header_scribbler)
    assert sha(f) == before and os.path.getmtime(f) == mtime
    assert not list((tmp_path / "b").glob("*"))


@needs_ffmpeg
def test_successful_write_then_exact_undo(copy_of, tmp_path):
    f = copy_of("film.mkv")
    before = sha(f)
    rec = embed.write(f, None, None, tmp_path / "b", "o1", _writer=tail_appender)
    assert rec["state"] == "done" and rec["mode"] == "inplace" and sha(f) != before
    embed.undo(f, rec)
    assert sha(f) == before


@needs_ffmpeg
def test_damaged_video_data_never_reaches_the_original_with_maximum_safety(copy_of, tmp_path):
    f = copy_of("film.mkv")
    before = sha(f)
    with pytest.raises(embed.EmbedError) as e:
        embed.write(f, None, None, tmp_path / "b", "o1", safe_copy=True, _writer=video_damager)
    assert e.value.code == "RNX-M007"
    assert sha(f) == before
    assert not list(tmp_path.glob("*.rennix-tag"))


@needs_ffmpeg
def test_damaged_video_data_is_detected_in_place(copy_of, tmp_path):
    f = copy_of("film.mkv")
    with pytest.raises(embed.EmbedError) as e:
        embed.write(f, None, None, tmp_path / "b", "o1", _writer=video_damager)
    assert e.value.code == "RNX-M007"


@needs_ffmpeg
def test_undo_refuses_if_the_file_changed_since(copy_of, tmp_path):
    f = copy_of("film.mkv")
    rec = embed.write(f, None, None, tmp_path / "b", "o1", _writer=tail_appender)
    with open(f, "ab") as fh:
        fh.write(b"\xec\x81\x00")                   # something else edited the file afterwards
    with pytest.raises(embed.EmbedError) as e:
        embed.undo(f, rec)
    assert e.value.code == "RNX-M008"


@needs_ffmpeg
@pytest.mark.skipif(os.name == "nt", reason="hardlink counts need admin-free NTFS links; covered on Linux and Mac")
def test_hardlinked_files_are_left_alone(copy_of, tmp_path):
    f = copy_of("film.mkv")
    os.link(f, tmp_path / "other.mkv")
    with pytest.raises(embed.EmbedError) as e:
        embed.write(f, None, None, tmp_path / "b", "o1", _writer=tail_appender)
    assert e.value.code == "RNX-M010"


# ---------------------------------------------------------------- pull the plug

def _killed_mid_write(path, backup_dir, journal):
    def ready(record):
        Path(journal).write_text(json.dumps(record))

    def die(p, kind):
        header_scribbler(p, kind)
        os._exit(9)                                  # the power goes off right here
    embed.write(path, None, None, Path(backup_dir), "o1", on_ready=ready, _writer=die)


def _killed_mid_copy(path, backup_dir, journal):
    def ready(record):
        Path(journal).write_text(json.dumps(record))

    def die(p, kind):
        header_scribbler(p, kind)
        os._exit(9)
    embed.write(path, None, None, Path(backup_dir), "o1", safe_copy=True, on_ready=ready, _writer=die)


@needs_ffmpeg
@pytest.mark.parametrize("target", [_killed_mid_write, _killed_mid_copy])
def test_pull_the_plug_then_recover(copy_of, tmp_path, target):
    f = copy_of("film.mkv")
    before = sha(f)
    journal = tmp_path / "journal.json"
    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(target=target, args=(str(f), str(tmp_path / "b"), str(journal)))
    p.start()
    p.join(60)
    assert p.exitcode == 9
    record = json.loads(journal.read_text())
    assert embed.recover(f, record) == "original"
    assert sha(f) == before
    assert not list(tmp_path.glob("*.rennix-tag"))


# ---------------------------------------------------------------- the real tools

@needs_mkvtool
@pytest.mark.parametrize("safe_copy", [False, True])
def test_mkvpropedit_writes_tags_and_cover_and_undoes_exactly(copy_of, tmp_path, safe_copy):
    from tests.fake_tmdb import FAKE_JPEG
    f = copy_of("film.mkv")
    before = sha(f)
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(FAKE_JPEG)
    fp = embed.fingerprint(f, "mkv")
    rec = embed.write(f, TAGS, cover, tmp_path / "b", "o1", safe_copy=safe_copy)
    s = ebml.read(f)
    assert s.title == "The Matrix"
    assert s.tags["TITLE"][0] == "The Matrix" and s.tags["ACTOR"] == ["Keanu Reeves, Carrie-Anne Moss"]
    assert s.tags["GENRE"] == ["Action, Science Fiction"]
    assert s.tags["SUMMARY"][0] == TAGS["plot"]
    assert any(n == "cover.jpg" for n, _ in s.attachments)
    assert not embed.same_media(fp, embed.fingerprint(f, "mkv"))     # video, audio, subs untouched
    embed.undo(f, rec)
    assert sha(f) == before


@needs_mkvtool
def test_existing_cover_is_kept_unless_asked(copy_of, tmp_path):
    from tests.fake_tmdb import FAKE_JPEG, FAKE_PNG
    f = copy_of("film.mkv")
    a, b = tmp_path / "a.jpg", tmp_path / "b.png"
    a.write_bytes(FAKE_JPEG)
    b.write_bytes(FAKE_PNG)
    embed.write(f, TAGS, a, tmp_path / "b1", "o1")
    rec = embed.write(f, TAGS, b, tmp_path / "b2", "o2")
    assert rec["note"] and [n for n, _ in ebml.read(f).attachments] == ["cover.jpg"]
    embed.write(f, TAGS, b, tmp_path / "b3", "o3", replace_cover=True)
    assert [n for n, _ in ebml.read(f).attachments] == ["cover.png"]


@needs_mutagen
def test_mp4_with_index_at_the_end_is_tagged_in_place_and_undone_exactly(copy_of, tmp_path):
    from mutagen.mp4 import MP4
    from tests.fake_tmdb import FAKE_JPEG
    f = copy_of("end.mp4")
    before = sha(f)
    cover = tmp_path / "c.jpg"
    cover.write_bytes(FAKE_JPEG)
    rec = embed.write(f, TAGS, cover, tmp_path / "b", "o1")
    assert rec["mode"] == "inplace"
    t = MP4(str(f)).tags
    assert t["©nam"] == ["The Matrix"] and t["covr"] and t["stik"] == [9]
    embed.undo(f, rec)
    assert sha(f) == before


@needs_mutagen
def test_mp4_with_index_at_the_front_switches_to_a_safe_copy(copy_of, tmp_path):
    from mutagen.mp4 import MP4
    f = copy_of("front.mp4")
    fp = embed.fingerprint(f, "mp4")
    rec = embed.write(f, TAGS, None, tmp_path / "b", "o1")
    assert rec["mode"] == "copy"                          # never shifts video data in the original
    assert MP4(str(f)).tags["©nam"] == ["The Matrix"]
    assert not embed.same_media(fp, embed.fingerprint(f, "mp4"))
    embed.undo(f, rec)
    assert "©nam" not in (MP4(str(f)).tags or {})
    assert not embed.same_media(fp, embed.fingerprint(f, "mp4"))


@needs_mutagen
def test_episode_tags_in_mp4(copy_of, tmp_path):
    from mutagen.mp4 import MP4
    f = copy_of("end.mp4")
    tags = {"kind": "episode", "title": "Breakage", "show": "Breaking Bad", "season": 2, "episode": 5}
    embed.write(f, tags, None, tmp_path / "b", "o1")
    t = MP4(str(f)).tags
    assert t["tvsh"] == ["Breaking Bad"] and t["tvsn"] == [2] and t["tves"] == [5] and t["stik"] == [10]


def test_mkv_tag_xml_escapes_everything():
    xml = embed.mkv_tags_xml(TAGS)
    assert "&lt;angle&gt;" in xml and "&amp;" in xml and xml.count("<Name>ACTOR</Name>") == 1
    assert "<String>Keanu Reeves, Carrie-Anne Moss</String>" in xml
    import xml.etree.ElementTree as ET
    ET.fromstring(xml.split("\n", 2)[2])


# ---------------------------------------------------------------- inside a real rename batch

@needs_ffmpeg
def test_tags_in_a_rename_batch_then_undo(media, tmp_path, monkeypatch):
    import time
    from rennix.renamer import Renamer
    from tests.test_metadata import make_api, files_under
    from tests.test_scanner_and_api import wait_idle
    monkeypatch.setattr(Renamer, "default_tag_writer", staticmethod(tail_appender))

    def media_only_verify(path, kind, before, tags, cover):
        # the stand-in writer doesn't really write tags; reading them back is tested with the real tools
        after = embed.fingerprint(path, kind)
        assert not embed.same_media(before, after)
        return after
    monkeypatch.setattr(embed, "_verify", media_only_verify)
    dl = tmp_path / "dl"
    dl.mkdir()
    shutil.copy2(media / "film.mkv", dl / "The.Matrix.1999.mkv")
    before = sha(dl / "The.Matrix.1999.mkv")
    api = make_api(tmp_path, server="plex", tags=True, cover=True)
    api.add_paths([str(dl)])
    time.sleep(0.05)
    wait_idle(api)
    api.rename()
    time.sleep(0.05)
    wait_idle(api)
    summary = [p for e, p in api._window.events if e == "renamed"][-1]
    assert summary["done"] == 1 and not summary["errors"], summary
    moved = tmp_path / "Movies" / "The Matrix (1999) {tmdb-603}" / "The Matrix (1999).mkv"
    assert sha(moved) != before
    batch = json.loads(next((tmp_path / "app").rglob("history/*.json")).read_text())
    tag_op = [o for o in batch["ops"] if o["kind"] == "tags"][0]
    assert tag_op["state"] == "done" and tag_op["payload"]["record"]["mode"] == "inplace"
    api.undo(api.history()[0]["id"])
    assert sha(dl / "The.Matrix.1999.mkv") == before


@needs_ffmpeg
def test_batch_interrupted_during_tag_writing_is_put_back(copy_of, tmp_path):
    """A batch whose journal says a tag write was under way: recovery restores the file exactly."""
    from rennix.config import Paths, atomic_write_json
    from rennix.renamer import Renamer
    f = copy_of("film.mkv")
    before = sha(f)
    paths = Paths(tmp_path / "app")
    bid = "20261108-120000-abcd"
    journal_rec = {}
    s = ebml.read(f)
    backup = embed.make_backup(f, "mkv", s, paths.backups / bid, "m1")
    journal_rec = {"container": "mkv", "mode": "inplace", "backup": backup,
                   "before": embed.fingerprint(f, "mkv"), "temp": "", "shifted": False}
    header_scribbler(f, "mkv")                        # the write had started when the power went
    batch = {"id": bid, "created": "2026-11-08T12:00:00", "label": "", "removed_dirs": [],
             "ops": [{"id": "m1", "file_id": "f1", "src": "", "dst": str(f), "action": "tag", "kind": "tags",
                      "state": "pending", "replace": False, "error": "", "code": "", "created_dirs": [],
                      "replaced_to_trash": False, "payload": {"file_ids": ["f1"], "record": journal_rec}}]}
    atomic_write_json(paths.journal_file, batch)
    Renamer(paths, {}).recover("reverse")
    assert sha(f) == before
    assert not paths.journal_file.exists()
