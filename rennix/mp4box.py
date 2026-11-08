"""A small, read-only MP4/M4V/MOV structure reader.

Used before and after mutagen writes tags, to know:

* where the movie index (``moov``) and the video data (``mdat``) sit, so Rennix
  can tell whether new tags fit without shifting the video data, and back up
  exactly the bytes that may change;
* fingerprints that must not change: the film's length and each track's
  header.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field

CONTAINERS = {b"moov", b"trak", b"mdia", b"udta", b"edts", b"minf"}


class Mp4Error(Exception):
    """The file isn't an MP4 file Rennix can safely work with."""


@dataclass
class Atom:
    kind: bytes
    offset: int
    size: int
    header: int

    @property
    def end(self) -> int:
        return self.offset + self.size

    @property
    def data(self) -> int:
        return self.offset + self.header


@dataclass
class Mp4Info:
    size: int
    atoms: list = field(default_factory=list)       # top level
    moov: Atom | None = None
    mdats: list = field(default_factory=list)
    fragmented: bool = False
    duration: tuple | None = None                     # (duration, timescale)
    track_hashes: list = field(default_factory=list)

    @property
    def mdat_after_moov(self) -> bool:
        return bool(self.moov) and any(m.offset > self.moov.offset for m in self.mdats)

    @property
    def media_range(self) -> tuple[int, int]:
        """Payload of the largest mdat (where the video lives)."""
        m = max(self.mdats, key=lambda a: a.size)
        return m.data, m.end


def _atoms(f, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        hlen = 8
        if size == 1:
            big = f.read(8)
            if len(big) < 8:
                raise Mp4Error("cut short")
            size = struct.unpack(">Q", big)[0]
            hlen = 16
        elif size == 0:
            size = end - pos
        if size < hlen or pos + size > end:
            raise Mp4Error("the file is cut short or damaged")
        yield Atom(kind, pos, size, hlen)
        pos += size


def is_mp4(path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(12)
        return len(head) == 12 and head[4:8] in (b"ftyp", b"moov", b"free", b"mdat", b"wide", b"skip")
    except OSError:
        return False


def read(path) -> Mp4Info:
    size = os.path.getsize(path)
    info = Mp4Info(size=size)
    with open(path, "rb") as f:
        for a in _atoms(f, 0, size):
            info.atoms.append(a)
            if a.kind == b"moov":
                if info.moov is not None:
                    raise Mp4Error("two movie indexes")
                info.moov = a
            elif a.kind == b"mdat":
                info.mdats.append(a)
            elif a.kind in (b"moof", b"mfra"):
                info.fragmented = True
        if info.moov is None:
            raise Mp4Error("no movie index (damaged or still downloading)")
        if not info.mdats:
            raise Mp4Error("no video data found")
        for a in _atoms(f, info.moov.data, info.moov.end):
            if a.kind == b"mvhd":
                f.seek(a.data)
                body = f.read(min(a.size - a.header, 40))
                if body and body[0] == 1 and len(body) >= 32:
                    ts, dur = struct.unpack(">IQ", body[20:32])
                elif len(body) >= 20:
                    ts, dur = struct.unpack(">II", body[12:20])
                else:
                    raise Mp4Error("bad movie header")
                info.duration = (dur, ts)
            elif a.kind == b"trak":
                h = hashlib.sha256()
                for t in _walk(f, a):
                    if t.kind in (b"tkhd", b"mdhd", b"hdlr", b"stsd"):
                        f.seek(t.offset)
                        h.update(t.kind + f.read(t.size))
                info.track_hashes.append(h.hexdigest())
    if info.duration is None:
        raise Mp4Error("no movie header")
    if not info.track_hashes:
        raise Mp4Error("no tracks")
    return info


def _walk(f, atom: Atom):
    for a in _atoms(f, atom.data, atom.end):
        yield a
        if a.kind in CONTAINERS:
            yield from _walk(f, a)
        elif a.kind == b"stbl":
            yield from _atoms(f, a.data, a.end)
