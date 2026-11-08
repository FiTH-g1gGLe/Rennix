"""A small, read-only Matroska (MKV/WebM) structure reader.

Rennix never writes MKV files itself (mkvpropedit does that). This reader is
used to check a file before and after tags are written:

* where the video data (the "clusters") starts and ends, so the parts that
  mkvpropedit may change (everything before and after the clusters) can be
  backed up byte for byte, and the video data can be sampled and compared;
* the exact bytes of the track list and the film's length, which must not
  change;
* the tags and attachments, to confirm what was written reads back.

It only reads element headers and skips over clusters, so even a 50 GB file
is checked in well under a second.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

EBML_HEADER = 0x1A45DFA3
SEGMENT = 0x18538067
CLUSTER = 0x1F43B675
INFO = 0x1549A966
TRACKS = 0x1654AE6B
TAGS = 0x1254C367
ATTACHMENTS = 0x1941A469
DURATION = 0x4489
TIMESCALE = 0x2AD7B1
TITLE = 0x7BA9
TAG = 0x7373
SIMPLE_TAG = 0x67C8
TAG_NAME = 0x45A3
TAG_STRING = 0x4487
ATTACHED_FILE = 0x61A7
FILE_NAME = 0x466E
FILE_MIME = 0x4660

UNKNOWN = -1


class MkvError(Exception):
    """The file isn't a Matroska file Rennix can safely work with."""


@dataclass
class MkvInfo:
    size: int
    segment_start: int = 0          # first byte of the segment's contents
    segment_end: int = 0
    first_cluster: int = 0          # offset of the first cluster element
    clusters_end: int = 0           # first byte after the last cluster
    clusters: int = 0
    tracks_hash: str = ""
    duration: float | None = None
    timescale: int = 1000000
    title: str = ""
    tags: dict = field(default_factory=dict)        # TAG NAME -> [values]
    attachments: list = field(default_factory=list)  # [(file name, mime)]

    @property
    def media_range(self) -> tuple[int, int]:
        return self.first_cluster, self.clusters_end


def _read_id(f) -> tuple[int, int]:
    b = f.read(1)
    if not b:
        raise EOFError
    first = b[0]
    length = 1
    mask = 0x80
    while length <= 4 and not first & mask:
        mask >>= 1
        length += 1
    if length > 4:
        raise MkvError("bad element id")
    rest = f.read(length - 1)
    if len(rest) != length - 1:
        raise EOFError
    value = first
    for c in rest:
        value = (value << 8) | c
    return value, length


def _read_size(f) -> tuple[int, int]:
    b = f.read(1)
    if not b:
        raise EOFError
    first = b[0]
    length = 1
    mask = 0x80
    while length <= 8 and not first & mask:
        mask >>= 1
        length += 1
    if length > 8:
        raise MkvError("bad element size")
    value = first & (mask - 1)
    all_ones = value == mask - 1
    rest = f.read(length - 1)
    if len(rest) != length - 1:
        raise EOFError
    for c in rest:
        value = (value << 8) | c
        all_ones = all_ones and c == 0xFF
    return (UNKNOWN if all_ones else value), length


def _header(f) -> tuple[int, int, int]:
    """Read one element header. Returns (id, data size, header length)."""
    eid, a = _read_id(f)
    size, b = _read_size(f)
    return eid, size, a + b


def _children(f, start: int, end: int):
    """Yield (id, element start, data start, data size) for elements in [start, end)."""
    pos = start
    while pos < end:
        f.seek(pos)
        try:
            eid, size, hlen = _header(f)
        except EOFError:
            return
        data = pos + hlen
        if size == UNKNOWN:
            raise MkvError("element with unknown size inside a container")
        yield eid, pos, data, size
        pos = data + size


def _uint(b: bytes) -> int:
    v = 0
    for c in b:
        v = (v << 8) | c
    return v


def _float(b: bytes) -> float | None:
    if len(b) == 4:
        return struct.unpack(">f", b)[0]
    if len(b) == 8:
        return struct.unpack(">d", b)[0]
    return None


def _text(b: bytes) -> str:
    return b.rstrip(b"\x00").decode("utf-8", "replace")


def is_mkv(path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x1a\x45\xdf\xa3"
    except OSError:
        return False


def read(path) -> MkvInfo:
    """Read the structure of an MKV file. Raises MkvError if it isn't safe to tag."""
    import os
    size = os.path.getsize(path)
    info = MkvInfo(size=size)
    with open(path, "rb") as f:
        try:
            eid, hsize, hlen = _header(f)
        except EOFError:
            raise MkvError("file too short")
        if eid != EBML_HEADER or hsize == UNKNOWN:
            raise MkvError("not a Matroska file")
        pos = hlen + hsize
        f.seek(pos)
        try:
            eid, ssize, slen = _header(f)
        except EOFError:
            raise MkvError("no segment")
        if eid != SEGMENT:
            raise MkvError("no segment")
        info.segment_start = pos + slen
        info.segment_end = size if ssize == UNKNOWN else info.segment_start + ssize
        if info.segment_end > size:
            raise MkvError("the file is shorter than it says (damaged or still downloading)")

        pos = info.segment_start
        while pos < info.segment_end:
            f.seek(pos)
            try:
                eid, esize, ehlen = _header(f)
            except EOFError:
                break
            data = pos + ehlen
            if esize == UNKNOWN:
                # live-recorded files: clusters without a size can't be skipped safely
                raise MkvError("a part of the file has no recorded size (live recording)")
            end = data + esize
            if end > info.segment_end:
                raise MkvError("the file is cut short (damaged or still downloading)")
            if eid == CLUSTER:
                if not info.clusters:
                    info.first_cluster = pos
                info.clusters += 1
                info.clusters_end = end
            elif eid == TRACKS:
                f.seek(pos)
                info.tracks_hash = hashlib.sha256(f.read(end - pos)).hexdigest()
            elif eid == INFO:
                _read_info(f, data, end, info)
            elif eid == TAGS:
                _read_tags(f, data, end, info)
            elif eid == ATTACHMENTS:
                _read_attachments(f, data, end, info)
            pos = end
    if not info.clusters:
        raise MkvError("no video data found")
    if not info.tracks_hash:
        raise MkvError("no track list found")
    return info


def _read_info(f, start, end, info):
    for eid, _, data, size in _children(f, start, end):
        if eid in (DURATION, TIMESCALE, TITLE) and size <= 1024:
            f.seek(data)
            raw = f.read(size)
            if eid == DURATION:
                info.duration = _float(raw)
            elif eid == TIMESCALE:
                info.timescale = _uint(raw)
            else:
                info.title = _text(raw)


def _read_tags(f, start, end, info):
    for eid, _, tdata, tsize in _children(f, start, end):
        if eid != TAG:
            continue
        for sid, _, sdata, ssize in _children(f, tdata, tdata + tsize):
            if sid != SIMPLE_TAG:
                continue
            name = value = None
            for cid, _, cdata, csize in _children(f, sdata, sdata + ssize):
                if cid in (TAG_NAME, TAG_STRING) and csize <= 1 << 20:
                    f.seek(cdata)
                    txt = _text(f.read(csize))
                    if cid == TAG_NAME:
                        name = txt.upper()
                    else:
                        value = txt
            if name:
                info.tags.setdefault(name, []).append(value or "")


def _read_attachments(f, start, end, info):
    for eid, _, adata, asize in _children(f, start, end):
        if eid != ATTACHED_FILE:
            continue
        name = mime = ""
        for cid, _, cdata, csize in _children(f, adata, adata + asize):
            if cid in (FILE_NAME, FILE_MIME) and csize <= 4096:
                f.seek(cdata)
                txt = _text(f.read(csize))
                if cid == FILE_NAME:
                    name = txt
                else:
                    mime = txt
        info.attachments.append((name, mime))
