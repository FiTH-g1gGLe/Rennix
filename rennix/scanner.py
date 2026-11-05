"""Find video files, the files that belong with them, and the junk to skip."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

VIDEO_EXTS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts",
    ".webm", ".flv", ".ogm", ".divx", ".vob", ".iso", ".3gp", ".rmvb",
}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".smi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tbn"}
INFO_EXTS = {".nfo", ".xml"}
COMPANION_EXTS = SUBTITLE_EXTS | IMAGE_EXTS | INFO_EXTS
JUNK_EXTS = {".txt", ".url", ".lnk", ".exe", ".website", ".html", ".htm", ".sfv",
             ".md5", ".par2", ".torrent", ".dat", ".db"}
GENERIC_IMAGES = {"poster", "folder", "cover", "fanart", "backdrop", "banner", "logo",
                  "clearart", "landscape", "thumb", "disc"}

_SAMPLE = re.compile(r"(?:^|[\W_])sample(?:[\W_]|$)", re.I)
_TRAILER = re.compile(r"(?:^|[\W_])(trailer|teaser)(?:[\W_]|$)", re.I)
_EXTRA = re.compile(
    r"(?:^|[\W_])(featurette|behind[\W_]?the[\W_]?scenes|deleted[\W_]?scenes?|interview|"
    r"making[\W_]?of|bloopers?|gag[\W_]?reel|extras?|bonus)(?:[\W_]|$)",
    re.I,
)
SAMPLE_MAX_BYTES = 350 * 1024 * 1024
SKIP_DIRS = {"$recycle.bin", "system volume information", ".git", "@eadir", ".trash",
             ".trashes", "#recycle", ".rennix"}


@dataclass
class Companion:
    path: Path
    suffix: str          # what follows the video's name, e.g. ".en.srt" or "-poster.jpg"
    generic: bool = False  # poster.jpg / folder.jpg style, kept by name in the new folder


@dataclass
class MediaFile:
    id: str
    path: Path
    size: int
    category: str = "video"    # video | sample | extra
    extra_type: str = ""       # trailer | featurette | ...
    companions: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class ScanResult:
    files: list = field(default_factory=list)       # MediaFile, videos to identify
    skipped: list = field(default_factory=list)     # (path, reason)
    junk: list = field(default_factory=list)        # Path
    errors: list = field(default_factory=list)      # (path, message)


def _classify(path: Path, size: int) -> tuple[str, str]:
    stem = path.stem
    parent = path.parent.name.lower()
    if parent in {"sample", "samples"} or (_SAMPLE.search(stem) and size < SAMPLE_MAX_BYTES):
        return "sample", ""
    m = _TRAILER.search(stem)
    if m and size < 600 * 1024 * 1024:
        return "extra", m.group(1).lower()
    if parent in {"featurettes", "extras", "behind the scenes", "deleted scenes",
                  "interviews", "trailers", "shorts", "other"}:
        return "extra", parent
    m = _EXTRA.search(stem)
    if m and size < 2 * 1024 ** 3:
        return "extra", m.group(1).lower()
    return "video", ""


def _iter_files(paths: Iterable[Path], errors: list) -> Iterable[Path]:
    for root in paths:
        root = Path(root)
        if root.is_file():
            yield root
            continue
        if not root.is_dir():
            errors.append((root, "Not found"))
            continue

        def onerror(exc: OSError, _errors=errors):
            _errors.append((Path(getattr(exc, "filename", "") or root), str(exc.strerror or exc)))

        for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
            dirnames[:] = sorted(
                d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")
            )
            for fn in sorted(filenames):
                if not fn.startswith("."):
                    yield Path(dirpath) / fn


def _companions_for(video: Path, siblings: list[Path], videos_in_dir: int) -> list[Companion]:
    out: list[Companion] = []
    stem_l = video.stem.lower()
    for s in siblings:
        if s == video or s.suffix.lower() not in COMPANION_EXTS:
            continue
        name_l = s.name.lower()
        if name_l.startswith(stem_l) and len(s.name) > len(video.stem):
            rest = s.name[len(video.stem):]
            if rest[:1] in {".", "-", "_", " "}:
                out.append(Companion(path=s, suffix=rest))
                continue
        if videos_in_dir == 1 and s.stem.lower() in GENERIC_IMAGES and s.suffix.lower() in IMAGE_EXTS:
            out.append(Companion(path=s, suffix=s.name, generic=True))
        elif videos_in_dir == 1 and s.suffix.lower() in SUBTITLE_EXTS and "subs" not in name_l:
            # a lone subtitle beside a lone video: e.g. English.srt -> keep language part
            out.append(Companion(path=s, suffix="." + s.name if not s.name.startswith(".") else s.name))
    return out


def scan(paths: Iterable[str | Path], include_samples: bool = False) -> ScanResult:
    """Walk files and folders and return the videos with their companion files."""
    result = ScanResult()
    by_dir: dict[Path, list[Path]] = {}
    for f in _iter_files([Path(p) for p in paths], result.errors):
        by_dir.setdefault(f.parent, []).append(f)

    counter = 0
    for folder, files in by_dir.items():
        try:
            siblings = sorted(p for p in folder.iterdir() if p.is_file())
        except OSError:
            siblings = files
        vids = [f for f in files if f.suffix.lower() in VIDEO_EXTS]
        real_videos = 0
        entries = []
        for v in vids:
            try:
                size = v.stat().st_size
            except OSError as exc:
                result.errors.append((v, str(exc)))
                continue
            category, extra_type = _classify(v, size)
            if category == "sample" and not include_samples:
                result.skipped.append((v, "Sample file"))
                continue
            if category == "video":
                real_videos += 1
            entries.append((v, size, category, extra_type))
        claimed: set[Path] = set()
        for v, size, category, extra_type in entries:
            counter += 1
            comps = _companions_for(v, siblings, real_videos if category == "video" else 2)
            comps = [c for c in comps if c.path not in claimed]
            claimed.update(c.path for c in comps)
            result.files.append(
                MediaFile(id=f"f{counter}", path=v, size=size, category=category,
                          extra_type=extra_type, companions=comps)
            )
        for f in files:
            if f.suffix.lower() in JUNK_EXTS and f not in claimed:
                result.junk.append(f)
    return result
