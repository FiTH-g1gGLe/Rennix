"""Read runtime, resolution and track details from a video's header.

Uses MediaInfo (via pymediainfo), which reads only the start of each file,
so a 50 GB file is as quick as a 1 GB one. Returns an empty dict if
MediaInfo isn't available or the file can't be read; identification then
simply works without runtime evidence.
"""

from __future__ import annotations

from pathlib import Path

try:
    from pymediainfo import MediaInfo
except ImportError:
    MediaInfo = None


def _resolution_label(width: int, height: int) -> str:
    if not width or not height:
        return ""
    if width >= 3200 or height >= 1800:
        return "2160p"
    if width >= 1800 or height >= 1000:
        return "1080p"
    if width >= 1200 or height >= 700:
        return "720p"
    if height >= 560:
        return "576p"
    return "480p"


def available() -> bool:
    if MediaInfo is None:
        return False
    try:
        return bool(MediaInfo.can_parse())
    except Exception:
        return False


def probe(path: str | Path) -> dict:
    """Return {'minutes', 'width', 'height', 'resolution', 'video_codec', 'hdr',
    'audio', 'channels', 'audio_languages', 'subtitle_languages', 'title'}."""
    if MediaInfo is None:
        return {}
    try:
        info = MediaInfo.parse(str(path), parse_speed=0.1)
    except Exception:
        return {}
    out: dict = {"audio_languages": [], "subtitle_languages": []}
    for track in info.tracks:
        tt = track.track_type
        if tt == "General":
            dur = track.duration
            try:
                out["minutes"] = round(float(dur) / 60000.0, 1) if dur else None
            except (TypeError, ValueError):
                out["minutes"] = None
            if track.title or track.movie_name:
                out["title"] = track.title or track.movie_name
        elif tt == "Video" and "width" not in out:
            out["width"] = int(track.width or 0)
            out["height"] = int(track.height or 0)
            out["resolution"] = _resolution_label(out["width"], out["height"])
            fmt = (track.format or "").upper()
            out["video_codec"] = {"AVC": "H.264", "HEVC": "HEVC", "AV1": "AV1"}.get(fmt, track.format or "")
            hdr = " ".join(str(x) for x in filter(None, [
                getattr(track, "hdr_format", None), getattr(track, "hdr_format_commercial", None),
                getattr(track, "hdr_format_compatibility", None), getattr(track, "hdr_format_profile", None),
                getattr(track, "transfer_characteristics", None)]))
            flags = []
            if "Dolby Vision" in hdr or "dvhe" in hdr or "dvh1" in hdr:
                flags.append("Dolby Vision")
            if "HDR10+" in hdr or "SMPTE ST 2094" in hdr:
                flags.append("HDR10+")
            elif "HDR10" in hdr or "PQ" in hdr or "SMPTE ST 2084" in hdr or "SMPTE ST 2086" in hdr:
                flags.append("HDR10")
            elif "HLG" in hdr or "ARIB STD-B67" in hdr:
                flags.append("HLG")
            out["hdr"] = flags
            out["hdr_checked"] = True   # the video was read, so "no flags" really means SDR
        elif tt == "Audio":
            if "audio" not in out:
                commercial = track.commercial_name or track.format or ""
                out["audio"] = "Atmos" if "Atmos" in commercial else (track.format or "")
                ch = track.channel_s
                try:
                    ch = int(str(ch).split()[0])
                    out["channels"] = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(ch, f"{ch}ch")
                except (TypeError, ValueError):
                    pass
            lang = (track.language or "").split("-")[0]
            if lang and lang not in out["audio_languages"]:
                out["audio_languages"].append(lang)
        elif tt == "Text":
            lang = (track.language or "").split("-")[0]
            if lang and lang not in out["subtitle_languages"]:
                out["subtitle_languages"].append(lang)
    return out
