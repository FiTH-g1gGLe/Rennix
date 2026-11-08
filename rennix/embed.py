"""Writing tags and a cover image INTO video files, without ever corrupting them.

This is the only part of Rennix that changes the inside of a video file, so it
is built around one rule: the video and audio data must come out byte-for-byte
identical, and if anything at all looks wrong the file goes back exactly as it
was.

Tools
-----
* MKV: ``mkvpropedit`` (MKVToolNix). It edits only the header parts of the
  file (title, tags, attachments) and never rewrites the video data.
* MP4: ``mutagen``. Rennix only lets it write in place when the new tags fit in
  the space already there (or nothing comes after the index), so it never
  shifts the video data along inside the original file.

Two ways to write
-----------------
* ``inplace`` (default): back up the exact bytes that may change, write, check.
* ``copy`` ("Maximum safety", and MP4 files without room): copy the file on the
  same drive, write to the copy, check it, then swap it in with one atomic
  rename. The original is untouched until the very last step.

Checks
------
Before: real file type from its contents, structure readable (not damaged or
half downloaded), not a hardlink or shortcut to another file, not read-only,
not in use, enough free space. A fingerprint is taken: length, track headers,
and 24 samples from across the video data.

After: the structure is read again; length, tracks and every sample must
match, and the tags must read back as written. Any failure restores the
backed-up bytes (or throws the copy away).

Every write leaves a record in the batch (and the crash journal) so it can be
undone exactly, or repaired after a crash.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from . import ebml, mp4box

log = logging.getLogger("rennix.embed")
IS_WINDOWS = os.name == "nt"
SAMPLES = 24
CHUNK = 64 * 1024
MAX_BACKUP = 256 * 1024 * 1024
SPARE = 64 * 1024 * 1024


class EmbedError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- tools

def find_mkvpropedit(custom: str = "") -> str | None:
    """Rennix ships mkvpropedit; a system copy or a chosen path also works."""
    exe = "mkvpropedit.exe" if IS_WINDOWS else "mkvpropedit"
    cands = []
    if custom:
        cands.append(Path(custom))
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    cands += [base / "tools" / exe, Path(sys.executable).parent / "tools" / exe]
    if IS_WINDOWS:
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(env):
                cands.append(Path(os.environ[env]) / "MKVToolNix" / exe)
    for c in cands:
        if c.is_file():
            return str(c)
    return shutil.which("mkvpropedit")


def have_mutagen() -> bool:
    try:
        import mutagen.mp4  # noqa: F401
        return True
    except ImportError:
        return False


def container_of(path) -> str | None:
    if ebml.is_mkv(path):
        return "mkv"
    if mp4box.is_mp4(path):
        return "mp4"
    return None


# --------------------------------------------------------------------------- fingerprints

def _structure(path, kind: str):
    try:
        return ebml.read(path) if kind == "mkv" else mp4box.read(path)
    except (ebml.MkvError, mp4box.Mp4Error) as exc:
        raise EmbedError("RNX-M002", f"The file looks damaged or incomplete ({exc}).")
    except OSError as exc:
        raise EmbedError("RNX-M004", f"The file couldn't be read ({exc}).")


def _samples(path, start: int, end: int) -> list[str]:
    length = max(0, end - start)
    out = []
    with open(path, "rb") as f:
        if length <= CHUNK * SAMPLES:
            f.seek(start)
            return [hashlib.sha256(f.read(length)).hexdigest()]
        for i in range(SAMPLES):
            f.seek(start + (length - CHUNK) * i // (SAMPLES - 1))
            out.append(hashlib.sha256(f.read(CHUNK)).hexdigest())
    return out


def fingerprint(path, kind: str) -> dict:
    s = _structure(path, kind)
    start, end = s.media_range
    fp = {"size": s.size, "media_start": start, "media_len": end - start,
          "samples": _samples(path, start, end)}
    if kind == "mkv":
        fp.update(tracks=s.tracks_hash, duration=s.duration, clusters=s.clusters)
    else:
        fp.update(tracks=s.track_hashes, duration=list(s.duration))
    return fp


def same_media(a: dict, b: dict) -> str:
    """Empty string if the video, audio and tracks are identical, else what differs."""
    for key, what in (("media_len", "the video data's length"), ("samples", "the video data"),
                      ("tracks", "the track list"), ("duration", "the running time"),
                      ("clusters", "the video data's layout")):
        if a.get(key) != b.get(key):
            return what
    return ""


# --------------------------------------------------------------------------- checks

def _in_use(path) -> bool:
    if not IS_WINDOWS:
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    GENERIC_RW = 0x80000000 | 0x40000000
    h = k32.CreateFileW(str(path), GENERIC_RW, 0, None, 3, 0x80, None)   # share mode 0 = exclusive
    if h == wintypes.HANDLE(-1).value:
        return ctypes.get_last_error() in (32, 33)
    k32.CloseHandle(h)
    return False


def _free(path) -> int:
    try:
        return shutil.disk_usage(str(Path(path).parent)).free
    except OSError:
        return 1 << 62


def preflight(path, kind: str, backup_bytes: int = 0, copy: bool = False) -> None:
    p = Path(path)
    if p.is_symlink():
        raise EmbedError("RNX-M010", "This is a shortcut (symlink) to another file, so its tags weren't changed.")
    if not p.is_file():
        raise EmbedError("RNX-M004", "The file couldn't be found.")
    st = p.stat()
    if getattr(st, "st_nlink", 1) > 1:
        raise EmbedError("RNX-M010", "This file is hardlinked to another copy, which would change too, "
                                     "so its tags weren't changed.")
    if not os.access(p, os.W_OK):
        raise EmbedError("RNX-M004", "The file is read-only.")
    if _in_use(p):
        raise EmbedError("RNX-M003", "The file is open in another program (perhaps playing in Plex).")
    need = SPARE + (st.st_size if copy else backup_bytes)
    if _free(p) < need:
        raise EmbedError("RNX-M005", "There isn't enough free space on that drive to write safely.")


# --------------------------------------------------------------------------- backups

def _regions(path, kind: str, structure) -> list[tuple[int, int]]:
    """The byte ranges a tag writer may change (everything except the video data)."""
    if kind == "mkv":
        return [(0, structure.first_cluster), (structure.clusters_end, structure.size)]
    moov = structure.moov
    end = moov.end if structure.mdat_after_moov else structure.size
    return [(moov.offset, end)]


def make_backup(path, kind: str, structure, folder: Path, name: str) -> dict:
    regions = [(a, b) for a, b in _regions(path, kind, structure) if b > a]
    total = sum(b - a for a, b in regions)
    if total > MAX_BACKUP:
        raise EmbedError("RNX-M002", "The file's header is unusually large, so Rennix won't change it.")
    folder.mkdir(parents=True, exist_ok=True)
    blob = folder / f"{name}.bin"
    segs = []
    with open(path, "rb") as src, open(blob, "wb") as out:
        pos = 0
        for a, b in regions:
            src.seek(a)
            data = src.read(b - a)
            out.write(data)
            segs.append({"offset": a, "length": len(data), "at": pos, "sha": hashlib.sha256(data).hexdigest()})
            pos += len(data)
        out.flush()
        os.fsync(out.fileno())
    st = os.stat(path)
    return {"blob": str(blob), "segments": segs, "size": structure.size,
            "atime": st.st_atime, "mtime": st.st_mtime}


def matches_backup(path, backup: dict) -> bool:
    """True if the file is still exactly as it was when backed up."""
    try:
        if os.path.getsize(path) != backup["size"]:
            return False
        with open(path, "rb") as f:
            for s in backup["segments"]:
                f.seek(s["offset"])
                if hashlib.sha256(f.read(s["length"])).hexdigest() != s["sha"]:
                    return False
        return True
    except OSError:
        return False


def restore_backup(path, backup: dict) -> None:
    """Put the backed-up bytes back and trim the file to its original size."""
    with open(backup["blob"], "rb") as src, open(path, "r+b") as f:
        for s in backup["segments"]:
            src.seek(s["at"])
            data = src.read(s["length"])
            if hashlib.sha256(data).hexdigest() != s["sha"]:
                raise EmbedError("RNX-U001", "The backup is damaged, so the file wasn't touched.")
            f.seek(s["offset"])
            f.write(data)
        f.truncate(backup["size"])
        f.flush()
        os.fsync(f.fileno())
    try:
        os.utime(path, (backup["atime"], backup["mtime"]))
    except OSError:
        pass


def drop_backup(backup: dict | None) -> None:
    for key in ("blob", "tags"):
        p = (backup or {}).get(key)
        if p:
            try:
                Path(p).unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- tag content

def mkv_tags_xml(tags: dict) -> str:
    def simple(name, value):
        return f"<Simple><Name>{escape(name)}</Name><String>{escape(str(value))}</String></Simple>"

    def tag(level, pairs):
        # lists go in as one value ("Action, Adventure"): many players only show one tag of each name
        body = "".join(simple(k, ", ".join(map(str, v)) if isinstance(v, list) else v)
                       for k, v in pairs if v not in (None, "", []))
        return f"<Tag><Targets><TargetTypeValue>{level}</TargetTypeValue></Targets>{body}</Tag>" if body else ""

    main = [("TITLE", tags.get("title")), ("DATE_RELEASED", tags.get("date")), ("GENRE", tags.get("genres", [])),
            ("SUMMARY", tags.get("plot")), ("COMMENT", tags.get("plot")),   # COMMENT: what Explorer add-ons show
            ("DIRECTOR", tags.get("directors", [])),
            ("WRITTEN_BY", tags.get("writers", [])), ("ACTOR", tags.get("cast", [])),
            ("LAW_RATING", tags.get("certification")), ("IMDB", tags.get("imdb")), ("TMDB", tags.get("tmdb"))]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE Tags SYSTEM "matroskatags.dtd">\n<Tags>']
    if tags.get("kind") == "episode":
        main.append(("PART_NUMBER", tags.get("episode")))
        xml.append(tag(70, [("TITLE", tags.get("show"))]))
        xml.append(tag(60, [("PART_NUMBER", tags.get("season"))]))
    xml.append(tag(50, main))
    xml.append("</Tags>\n")
    return "".join(xml)


def _mp4_values(tags: dict, cover: bytes | None) -> dict:
    from mutagen.mp4 import MP4Cover, MP4FreeForm
    import plistlib
    v = {}
    put = lambda k, x: v.__setitem__(k, x) if x not in (None, "", []) else None
    put("©nam", [tags.get("title")] if tags.get("title") else None)
    put("©day", [tags.get("date")] if tags.get("date") else None)
    put("©gen", [", ".join(tags["genres"])] if tags.get("genres") else None)
    plot = tags.get("plot") or ""
    put("desc", [plot[:255]] if plot else None)
    put("ldes", [plot] if plot else None)
    put("©ART", [", ".join(tags.get("directors") or [])] if tags.get("directors") else None)
    if tags.get("kind") == "episode":
        v["stik"] = [10]
        put("tvsh", [tags.get("show")] if tags.get("show") else None)
        if tags.get("season") is not None:
            v["tvsn"] = [int(tags["season"])]
        if tags.get("episode") is not None:
            v["tves"] = [int(tags["episode"])]
    else:
        v["stik"] = [9]
    movi = {k: [{"name": n} for n in tags.get(src) or []]
            for k, src in (("cast", "cast"), ("directors", "directors"), ("screenwriters", "writers"))}
    movi = {k: x for k, x in movi.items() if x}
    if tags.get("studio"):
        movi["studio"] = tags["studio"]
    if movi:
        v["----:com.apple.iTunes:iTunMOVI"] = [MP4FreeForm(plistlib.dumps(movi))]
    if cover:
        fmt = MP4Cover.FORMAT_PNG if cover[:4] == b"\x89PNG" else MP4Cover.FORMAT_JPEG
        v["covr"] = [MP4Cover(cover, imageformat=fmt)]
    return v


# --------------------------------------------------------------------------- writers

class _NeedsCopy(Exception):
    pass


def _run_mkvpropedit(tool: str, path, args: list[str]) -> None:
    flags = 0x08000000 if IS_WINDOWS else 0            # CREATE_NO_WINDOW
    try:
        r = subprocess.run([tool, "--flush-on-close", str(path), *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=180, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EmbedError("RNX-M006", f"mkvpropedit couldn't run ({exc}).")
    if r.returncode not in (0, 1):                    # 1 = finished with warnings
        msg = (r.stdout + r.stderr).strip().splitlines()
        raise EmbedError("RNX-M006", "mkvpropedit reported a problem: " + (msg[-1] if msg else f"code {r.returncode}"))
    if r.returncode == 1:
        log.warning("mkvpropedit warnings for %s: %s", Path(path).name, r.stdout.strip()[-400:])


def _write_mkv(path, tags: dict, cover: Path | None, tool: str, structure, replace_cover: bool,
               scratch: Path) -> dict:
    scratch.mkdir(parents=True, exist_ok=True)
    xml = scratch / "tags.xml"
    args = []
    if tags:
        xml.write_text(mkv_tags_xml(tags), encoding="utf-8")
        args = ["--tags", f"global:{xml}"]
        if tags.get("title"):
            args += ["--edit", "info", "--set", f"title={tags['title']}"]
    note = ""
    if cover:
        ext = ".png" if cover.read_bytes()[:4] == b"\x89PNG" else ".jpg"
        name, mime = "cover" + ext, ("image/png" if ext == ".png" else "image/jpeg")
        existing = [n for n, _ in structure.attachments if n.lower().startswith("cover.")]
        common = ["--attachment-name", name, "--attachment-mime-type", mime]
        if not existing:
            args += [*common, "--add-attachment", str(cover)]
        elif replace_cover:
            args += [*common, "--replace-attachment", f"name:{existing[0]}:{cover}"]
        else:
            note = "kept the cover already in the file"
    if not args:
        return {"note": note, "nothing": True}
    _run_mkvpropedit(tool, path, args)
    return {"note": note}


def _write_mp4(path, tags: dict, cover: Path | None, replace_cover: bool, allow_shift: bool) -> dict:
    from mutagen.mp4 import MP4
    mp = MP4(str(path))
    if mp.tags is None:
        mp.add_tags()
    note = ""
    data = cover.read_bytes() if cover else None
    if data and "covr" in mp.tags and not replace_cover:
        data, note = None, "kept the cover already in the file"
    values = _mp4_values(tags, data) if tags else {}
    if not tags and data:
        values = {k: v for k, v in _mp4_values({}, data).items() if k == "covr"}
    if not values:
        return {"note": note, "nothing": True}
    for k, val in values.items():
        mp.tags[k] = val
    shift_ok = allow_shift or not mp4box.read(path).mdat_after_moov

    def padding(info):
        if info.padding >= 0:
            return info.padding                  # fits: nothing after it moves
        if not shift_ok:
            raise _NeedsCopy()                   # would shift the video data: never in place
        return 64 * 1024

    mp.save(padding=padding)
    return {"note": note}


def _mp4_tags_snapshot(path) -> dict:
    from mutagen.mp4 import MP4
    mp = MP4(str(path))
    return dict(mp.tags or {})


def _mp4_restore_tags(path, snapshot: dict) -> None:
    from mutagen.mp4 import MP4
    mp = MP4(str(path))
    if mp.tags is None:
        mp.add_tags()
    mp.tags.clear()
    mp.tags.update(snapshot)
    mp.save(padding=lambda info: max(info.padding, 0) if info.padding >= 0 else 64 * 1024)


# --------------------------------------------------------------------------- verification

def _verify(path, kind: str, before: dict, tags: dict, cover_expected: bool) -> dict:
    after = fingerprint(path, kind)
    diff = same_media(before, after)
    if diff:
        raise EmbedError("RNX-M007", f"After writing, {diff} didn't match the original.")
    title = (tags or {}).get("title") or ""
    if kind == "mkv":
        s = ebml.read(path)
        if title and title not in s.tags.get("TITLE", []) and s.title != title:
            raise EmbedError("RNX-M007", "The new tags didn't read back from the file.")
        if cover_expected and not any(n.lower().startswith("cover.") for n, _ in s.attachments):
            raise EmbedError("RNX-M007", "The cover didn't read back from the file.")
    else:
        from mutagen.mp4 import MP4
        t = MP4(str(path)).tags or {}
        if title and (t.get("©nam") or [""])[0] != title:
            raise EmbedError("RNX-M007", "The new tags didn't read back from the file.")
        if cover_expected and not t.get("covr"):
            raise EmbedError("RNX-M007", "The cover didn't read back from the file.")
    return after


# --------------------------------------------------------------------------- the main entry

def write(path, tags: dict, cover: Path | None, backup_dir: Path, name: str, *, safe_copy: bool = False,
          replace_cover: bool = False, tool: str | None = None, on_ready: Callable[[dict], None] | None = None,
          _writer: Callable | None = None) -> dict:
    """Write tags (and optionally a cover) into a video file, safely.

    ``on_ready`` is called with the undo/recovery record after the backup is
    made and BEFORE the file is touched, so it can go into the crash journal.
    Returns the record (with ``state`` = done).
    """
    path = Path(path)
    kind = container_of(path)
    if kind is None:
        raise EmbedError("RNX-M002", "Only MKV and MP4 files can have tags written into them.")
    if kind == "mkv":
        tool = tool or find_mkvpropedit()
        if not tool and _writer is None:
            raise EmbedError("RNX-M001", "mkvpropedit, the tool for MKV files, couldn't be found.")
    elif not have_mutagen() and _writer is None:
        raise EmbedError("RNX-M001", "The MP4 tagging component (mutagen) is missing.")

    structure = _structure(path, kind)
    before = fingerprint(path, kind)
    backup_bytes = sum(b - a for a, b in _regions(path, kind, structure))
    preflight(path, kind, backup_bytes, copy=safe_copy)
    started = time.time()

    backup = make_backup(path, kind, structure, backup_dir, name)
    if kind == "mp4" and _writer is None:
        snap = backup_dir / f"{name}.tags"
        with open(snap, "wb") as fh:
            pickle.dump(_mp4_tags_snapshot(path), fh)
        backup["tags"] = str(snap)
    record = {"container": kind, "mode": "copy" if safe_copy else "inplace", "backup": backup,
              "before": before, "temp": "", "shifted": False, "note": ""}

    def apply(target: Path, allow_shift: bool) -> dict:
        if _writer is not None:
            return _writer(target, kind) or {}
        if kind == "mkv":
            return _write_mkv(target, tags, cover, tool, ebml.read(target), replace_cover,
                              backup_dir / f"{name}-scratch")
        return _write_mp4(target, tags, cover, replace_cover, allow_shift)

    def notify():
        if on_ready:
            on_ready(json.loads(json.dumps(record)))

    try:
        if not safe_copy:
            notify()
            try:
                res = apply(path, allow_shift=False)
            except _NeedsCopy:
                # MP4 without room: nothing has been written yet; switch to a safe copy
                log.info("%s: new tags don't fit in place, using a safe copy", path.name)
                preflight(path, kind, copy=True)
                record["mode"] = "copy"
                return _finish_copy(path, kind, tags, cover, record, apply, notify, started)
            try:
                record["after"] = _verify(path, kind, before, tags, bool(cover) and not res.get("note"))
            except EmbedError:
                restore_backup(path, backup)
                raise
            except Exception as exc:
                restore_backup(path, backup)
                raise EmbedError("RNX-M007", f"The file couldn't be checked after writing ({exc}).")
            record["note"] = res.get("note", "")
            _keep_times(path, backup)
            record["post_size"] = os.path.getsize(path)
            record["state"] = "done"
            log.info("Tags written into %s in place (%.1fs)", path.name, time.time() - started)
            return record
        return _finish_copy(path, kind, tags, cover, record, apply, notify, started)
    except EmbedError:
        if record["mode"] == "inplace" and not matches_backup(path, backup):
            restore_backup(path, backup)
        drop_backup(backup)
        raise
    except Exception as exc:
        # anything unexpected: make sure the original is exactly as it was
        if record["mode"] == "inplace" and not matches_backup(path, backup):
            restore_backup(path, backup)
        drop_backup(backup)
        raise EmbedError("RNX-M006", f"Writing tags failed ({exc}). The file was put back as it was.")


def _keep_times(path, backup) -> None:
    try:
        os.utime(path, (backup["atime"], backup["mtime"]))   # keep "Date modified" as it was
    except OSError:
        pass


def _finish_copy(path: Path, kind, tags, cover, record, apply, notify, started) -> dict:
    temp = path.with_name(path.name + ".rennix-tag")
    record["temp"] = str(temp)
    notify()
    try:
        if temp.exists():
            temp.unlink()
        shutil.copyfile(path, temp)
        with open(temp, "rb+") as fh:
            os.fsync(fh.fileno())
        if os.path.getsize(temp) != os.path.getsize(path):
            raise EmbedError("RNX-M006", "The safe copy didn't come out the same size.")
        res = apply(temp, allow_shift=True)
        after = _verify(temp, kind, record["before"], tags, bool(cover) and not res.get("note"))
        _keep_times(temp, record["backup"])
        record["shifted"] = after["media_start"] != record["before"]["media_start"]
        os.replace(temp, path)                     # one atomic step: old file or new file, never half
    except BaseException:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        raise
    record.update(after=after, note=res.get("note", ""), post_size=os.path.getsize(path), state="done")
    log.info("Tags written into %s using a safe copy (%.1fs)", path.name, time.time() - started)
    return record


# --------------------------------------------------------------------------- undo and recovery

def undo(path, record: dict) -> None:
    """Put the original tags back exactly. Refuses if the file changed since."""
    path = Path(path)
    kind = record["container"]
    backup = record["backup"]
    now = fingerprint(path, kind)
    if now.get("size") != record.get("post_size") or same_media(now, record.get("after") or {}):
        raise EmbedError("RNX-M008", "The file has changed since Rennix wrote its tags, so they were left alone.")
    if not record.get("shifted"):
        restore_backup(path, backup)
        check = fingerprint(path, kind)
        if same_media(check, record["before"]) or not matches_backup(path, backup):
            raise EmbedError("RNX-U001", "The original tags couldn't be put back exactly.")
    else:
        # MP4 whose index grew in a safe copy: restore the old tags the same safe way
        with open(backup["tags"], "rb") as fh:
            snap = pickle.load(fh)
        temp = path.with_name(path.name + ".rennix-tag")
        shutil.copyfile(path, temp)
        try:
            _mp4_restore_tags(temp, snap)
            if same_media(fingerprint(temp, kind), record["before"]):
                raise EmbedError("RNX-U001", "The original tags couldn't be put back safely.")
            _keep_times(temp, backup)
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
    drop_backup(backup)


def recover(path, record: dict) -> str:
    """After a crash during a write. Returns 'original' or 'done'."""
    path = Path(path)
    temp = Path(record["temp"]) if record.get("temp") else None
    if temp and temp.exists():
        try:
            temp.unlink()                           # an unfinished copy: the original was never touched
        except OSError:
            pass
    backup = record["backup"]
    if matches_backup(path, backup):
        drop_backup(backup)
        return "original"
    if record.get("mode") == "copy":
        # the swap is atomic, so a changed file is the finished, checked copy
        try:
            if not same_media(fingerprint(path, record["container"]), record["before"]):
                return "done"
        except EmbedError:
            pass
    restore_backup(path, backup)
    drop_backup(backup)
    return "original"
