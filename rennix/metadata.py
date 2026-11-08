"""Version 2: saving metadata (posters, logos, .nfo files and tags) with a rename.

Everything here is optional: one main switch (off by default) and one switch
per item. Files are named for the chosen media server.

Flow
----
1. ``plan_ops``: after the rename plan is built, fetch full details from
   TheMovieDB and add one operation per thing to save (kind ``meta`` for a file
   next to the video, kind ``tags`` for writing into the video).
2. ``prefetch``: download every image to a staging folder and write each .nfo
   there, so the batch itself only does quick, local, checkable steps.
3. The renamer runs the operations after the videos have moved, records them in
   the crash journal and history, and Undo reverses them.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .scanner import VIDEO_EXTS
from .textnorm import normalize

log = logging.getLogger("rennix.metadata")
IMG = "https://image.tmdb.org/t/p/original"
SEASON_DIR = re.compile(r"^(season\s*\d+|specials|s\d{1,2})$", re.I)
WRITER_JOBS = {"Screenplay", "Writer", "Story", "Novel", "Teleplay", "Author", "Characters"}
ITEMS = ("poster", "logo", "nfo", "season_posters", "episode_thumbs", "tags", "cover")

# What the main switch pre-ticks the first time it's turned on, per media server.
PRESETS = {
    "plex": {"poster": True, "logo": False, "nfo": False, "season_posters": False, "episode_thumbs": False},
    "jellyfin": {"poster": True, "logo": True, "nfo": True, "season_posters": True, "episode_thumbs": True},
    "emby": {"poster": True, "logo": True, "nfo": True, "season_posters": True, "episode_thumbs": True},
    "kodi": {"poster": True, "logo": True, "nfo": True, "season_posters": True, "episode_thumbs": True},
    "none": {"poster": True, "logo": True, "nfo": True, "season_posters": True, "episode_thumbs": True},
}

LABELS = {"poster": "Poster", "show_poster": "Show poster", "logo": "Logo", "show_logo": "Show logo",
          "nfo": ".nfo", "tvshow_nfo": "Show .nfo", "season_poster": "Season poster",
          "episode_thumb": "Episode thumbnail", "tags": "Tags", "cover": "Cover"}


def preset_for(server: str) -> dict:
    return dict(PRESETS.get(server or "plex", PRESETS["plex"]), tags=False, cover=False)


# --------------------------------------------------------------------------- details

def _year(date: str) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def _cert_movie(d: dict, region: str) -> str:
    by = {r.get("iso_3166_1"): r.get("release_dates", []) for r in (d.get("release_dates") or {}).get("results", [])}
    for country in dict.fromkeys([region, "US"]):
        rels = sorted(by.get(country, []), key=lambda r: (r.get("type") != 3, r.get("type", 9)))
        for r in rels:
            if r.get("certification"):
                return r["certification"].strip()
    return ""


def _cert_tv(d: dict, region: str) -> str:
    by = {r.get("iso_3166_1"): r.get("rating", "") for r in (d.get("content_ratings") or {}).get("results", [])}
    for country in dict.fromkeys([region, "US"]):
        if by.get(country):
            return by[country].strip()
    return ""


def _people(credits: dict, jobs: set) -> list[str]:
    return list(dict.fromkeys(c["name"] for c in credits.get("crew", []) if c.get("job") in jobs and c.get("name")))


def _cast(credits: dict, limit: int = 15) -> list[dict]:
    out = []
    for c in sorted(credits.get("cast", []), key=lambda c: c.get("order", 999))[:limit]:
        if c.get("name"):
            out.append({"name": c["name"], "role": c.get("character") or "", "order": c.get("order", len(out)),
                        "thumb": IMG + c["profile_path"] if c.get("profile_path") else ""})
    return out


def _logo(d: dict, language: str) -> str:
    lang = (language or "en").split("-")[0]
    logos = [l for l in (d.get("images") or {}).get("logos", []) if str(l.get("file_path", "")).endswith(".png")]
    rank = {lang: 0, "en": 1, None: 2}
    logos.sort(key=lambda l: (rank.get(l.get("iso_639_1"), 3), -(l.get("vote_average") or 0)))
    return IMG + logos[0]["file_path"] if logos else ""


def _trailer(d: dict) -> str:
    vids = [v for v in (d.get("videos") or {}).get("results", []) if v.get("site") == "YouTube" and v.get("type") == "Trailer"]
    vids.sort(key=lambda v: not v.get("official"))
    return f"plugin://plugin.video.youtube/?action=play_video&videoid={vids[0]['key']}" if vids else ""


def movie_info(d: dict, region: str = "GB", language: str = "en-US") -> dict:
    credits = d.get("credits") or {}
    ext = d.get("external_ids") or {}
    return {
        "kind": "movie", "tmdb_id": d.get("id"), "title": d.get("title") or "",
        "original_title": d.get("original_title") or "", "year": _year(d.get("release_date", "")),
        "premiered": d.get("release_date") or "", "tagline": d.get("tagline") or "", "plot": d.get("overview") or "",
        "runtime": d.get("runtime") or None, "genres": [g["name"] for g in d.get("genres", [])],
        "studios": [c["name"] for c in d.get("production_companies", [])][:3],
        "countries": [c["name"] for c in d.get("production_countries", [])],
        "rating": d.get("vote_average"), "votes": d.get("vote_count"),
        "certification": _cert_movie(d, region), "directors": _people(credits, {"Director"}),
        "writers": _people(credits, WRITER_JOBS), "cast": _cast(credits),
        "collection": (d.get("belongs_to_collection") or {}).get("name", ""),
        "trailer": _trailer(d), "imdb": d.get("imdb_id") or ext.get("imdb_id") or "",
        "poster": IMG + d["poster_path"] if d.get("poster_path") else "", "logo": _logo(d, language),
    }


def show_info(d: dict, region: str = "GB", language: str = "en-US") -> dict:
    credits = d.get("credits") or {}
    ext = d.get("external_ids") or {}
    return {
        "kind": "show", "tmdb_id": d.get("id"), "title": d.get("name") or "",
        "original_title": d.get("original_name") or "", "year": _year(d.get("first_air_date", "")),
        "premiered": d.get("first_air_date") or "", "plot": d.get("overview") or "",
        "genres": [g["name"] for g in d.get("genres", [])], "studios": [n["name"] for n in d.get("networks", [])][:3],
        "rating": d.get("vote_average"), "votes": d.get("vote_count"), "status": d.get("status") or "",
        "certification": _cert_tv(d, region), "cast": _cast(credits),
        "imdb": ext.get("imdb_id") or "", "tvdb": ext.get("tvdb_id") or "",
        "poster": IMG + d["poster_path"] if d.get("poster_path") else "", "logo": _logo(d, language),
        "season_posters": {s.get("season_number"): IMG + s["poster_path"]
                           for s in d.get("seasons", []) if s.get("poster_path")},
    }


def episode_info(season: dict, number: int) -> dict:
    for e in season.get("episodes", []):
        if e.get("episode_number") == number:
            crew = {"crew": e.get("crew", [])}
            return {
                "kind": "episode", "tmdb_id": e.get("id"), "title": e.get("name") or "",
                "season": e.get("season_number"), "episode": number, "plot": e.get("overview") or "",
                "aired": e.get("air_date") or "", "runtime": e.get("runtime") or None,
                "rating": e.get("vote_average"), "votes": e.get("vote_count"),
                "directors": _people(crew, {"Director"}), "writers": _people(crew, WRITER_JOBS),
                "guests": _cast({"cast": e.get("guest_stars", [])}, 10),
                "thumb": IMG + e["still_path"] if e.get("still_path") else "",
            }
    return {}


# --------------------------------------------------------------------------- .nfo files

def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text not in (None, ""):
        el.text = str(text)
    return el


def _ratings(root, rating, votes):
    if rating:
        r = _sub(_sub(root, "ratings"), "rating", name="themoviedb", max="10", default="true")
        _sub(r, "value", f"{float(rating):.1f}")
        if votes:
            _sub(r, "votes", votes)


def _actors(root, cast):
    for c in cast:
        a = _sub(root, "actor")
        _sub(a, "name", c["name"])
        _sub(a, "role", c.get("role"))
        _sub(a, "order", c.get("order"))
        _sub(a, "thumb", c.get("thumb"))


def _xml(root) -> str:
    ET.indent(root, space="  ")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            "<!-- Saved by Rennix from TheMovieDB -->\n" + ET.tostring(root, encoding="unicode") + "\n")


def movie_nfo(m: dict) -> str:
    root = ET.Element("movie")
    _sub(root, "title", m["title"])
    _sub(root, "originaltitle", m.get("original_title"))
    _ratings(root, m.get("rating"), m.get("votes"))
    _sub(root, "plot", m.get("plot"))
    _sub(root, "tagline", m.get("tagline"))
    _sub(root, "runtime", m.get("runtime"))
    if m.get("poster"):
        _sub(root, "thumb", m["poster"], aspect="poster")
    _sub(root, "mpaa", m.get("certification"))
    _sub(root, "uniqueid", m.get("tmdb_id"), type="tmdb", default="true")
    if m.get("imdb"):
        _sub(root, "uniqueid", m["imdb"], type="imdb")
    for g in m.get("genres", []):
        _sub(root, "genre", g)
    for c in m.get("countries", []):
        _sub(root, "country", c)
    if m.get("collection"):
        _sub(_sub(root, "set"), "name", m["collection"])
    for w in m.get("writers", []):
        _sub(root, "credits", w)
    for d in m.get("directors", []):
        _sub(root, "director", d)
    _sub(root, "premiered", m.get("premiered"))
    _sub(root, "year", m.get("year"))
    for s in m.get("studios", []):
        _sub(root, "studio", s)
    _sub(root, "trailer", m.get("trailer"))
    _actors(root, m.get("cast", []))
    return _xml(root)


def tvshow_nfo(s: dict) -> str:
    root = ET.Element("tvshow")
    _sub(root, "title", s["title"])
    _sub(root, "originaltitle", s.get("original_title"))
    _sub(root, "showtitle", s["title"])
    _ratings(root, s.get("rating"), s.get("votes"))
    _sub(root, "plot", s.get("plot"))
    if s.get("poster"):
        _sub(root, "thumb", s["poster"], aspect="poster")
    _sub(root, "mpaa", s.get("certification"))
    _sub(root, "uniqueid", s.get("tmdb_id"), type="tmdb", default="true")
    if s.get("imdb"):
        _sub(root, "uniqueid", s["imdb"], type="imdb")
    if s.get("tvdb"):
        _sub(root, "uniqueid", s["tvdb"], type="tvdb")
    for g in s.get("genres", []):
        _sub(root, "genre", g)
    _sub(root, "premiered", s.get("premiered"))
    _sub(root, "year", s.get("year"))
    _sub(root, "status", s.get("status"))
    for st in s.get("studios", []):
        _sub(root, "studio", st)
    _actors(root, s.get("cast", []))
    return _xml(root)


def episode_nfo(show: dict, eps: list[dict]) -> str:
    """One <episodedetails> per episode (double episodes get two, as Kodi expects)."""
    parts = []
    for e in eps:
        root = ET.Element("episodedetails")
        _sub(root, "title", e.get("title"))
        _sub(root, "showtitle", show.get("title"))
        _sub(root, "season", e.get("season"))
        _sub(root, "episode", e.get("episode"))
        _ratings(root, e.get("rating"), e.get("votes"))
        _sub(root, "plot", e.get("plot"))
        _sub(root, "runtime", e.get("runtime"))
        if e.get("thumb"):
            _sub(root, "thumb", e["thumb"])
        _sub(root, "uniqueid", e.get("tmdb_id"), type="tmdb", default="true")
        for w in e.get("writers", []):
            _sub(root, "credits", w)
        for d in e.get("directors", []):
            _sub(root, "director", d)
        _sub(root, "aired", e.get("aired"))
        _actors(root, e.get("guests", []))
        parts.append(_xml(root))
    if not parts:
        return ""
    head = parts[0]
    return head + "".join(p.split("-->\n", 1)[1] for p in parts[1:])


# --------------------------------------------------------------------------- tags for inside the file

def movie_tags(m: dict) -> dict:
    return {"kind": "movie", "title": m["title"], "date": m.get("premiered") or (str(m["year"]) if m.get("year") else ""),
            "genres": m.get("genres", []), "plot": m.get("plot"), "directors": m.get("directors", []),
            "writers": m.get("writers", []), "cast": [c["name"] for c in m.get("cast", [])][:15],
            "certification": m.get("certification"), "imdb": m.get("imdb"), "tmdb": f"movie/{m.get('tmdb_id')}",
            "studio": (m.get("studios") or [""])[0]}


def episode_tags(show: dict, eps: list[dict]) -> dict:
    e = eps[0]
    title = " & ".join(x.get("title", "") for x in eps if x.get("title")) or show["title"]
    return {"kind": "episode", "title": title, "show": show["title"], "season": e.get("season"),
            "episode": e.get("episode"), "date": e.get("aired") or "", "genres": show.get("genres", []),
            "plot": e.get("plot") or show.get("plot"), "directors": e.get("directors", []),
            "writers": e.get("writers", []), "cast": [c["name"] for c in show.get("cast", [])][:15],
            "certification": show.get("certification"), "imdb": show.get("imdb"),
            "tmdb": f"tv/{show.get('tmdb_id')}", "studio": (show.get("studios") or [""])[0]}


# --------------------------------------------------------------------------- where files go

def _ext(url: str, default: str = ".jpg") -> str:
    e = os.path.splitext(url)[1].lower()
    return e if e in (".jpg", ".jpeg", ".png") else default


def own_folder(dst: Path, title: str, alone: bool) -> bool:
    """Does this video have a folder to itself, named after it?"""
    t = normalize(title)
    return alone and bool(t) and normalize(dst.parent.name).startswith(t)


def movie_targets(dst: Path, server: str, own: bool, info: dict) -> dict:
    stem = dst.stem
    out = {}
    if info.get("poster"):
        ext = _ext(info["poster"])
        if own:
            out["poster"] = (dst.parent / f"poster{ext}", info["poster"])
        else:
            out["poster"] = (dst.parent / (f"{stem}{ext}" if server == "plex" else f"{stem}-poster{ext}"), info["poster"])
    if info.get("logo"):
        if own:
            out["logo"] = (dst.parent / ("clearlogo.png" if server == "kodi" else "logo.png"), info["logo"])
        elif server != "plex":
            out["logo"] = (dst.parent / (f"{stem}-clearlogo.png" if server == "kodi" else f"{stem}-logo.png"), info["logo"])
    out["nfo"] = (dst.parent / f"{stem}.nfo", None)
    return out


def show_dirs(dst: Path, show_title: str) -> tuple[Path | None, Path | None]:
    """(show folder, season folder) for an episode, or None where there isn't one."""
    parent = dst.parent
    if SEASON_DIR.match(parent.name.strip()):
        return parent.parent, parent
    t = normalize(show_title)
    if t and normalize(parent.name).startswith(t):
        return parent, None
    return None, None


def season_poster_target(server: str, show_dir: Path | None, season_dir: Path | None, season: int, url: str):
    ext = _ext(url)
    if server == "plex":
        if season_dir is None:
            return None
        name = "season-specials-poster" if season == 0 else f"Season{season:02d}"
        return season_dir / f"{name}{ext}"
    if show_dir is None:
        return None
    name = "season-specials-poster" if season == 0 else f"season{season:02d}-poster"
    return show_dir / f"{name}{ext}"


# --------------------------------------------------------------------------- planning

def wanted(settings: dict) -> dict:
    md = settings.get("metadata", {})
    if not md.get("enabled"):
        return {}
    return {k: bool(md.get(k)) for k in ITEMS}


def plan_ops(plan, items: list, settings: dict, client, *, exists=os.path.exists, progress=None) -> int:
    """Add metadata operations to a rename plan. Returns how many files get metadata."""
    from .renamer import Op
    want = wanted(settings)
    if not any(want.values()):
        return 0
    md = settings.get("metadata", {})
    server = settings.get("general", {}).get("media_server", "plex")
    region = settings.get("matching", {}).get("region", "GB")
    language = settings.get("matching", {}).get("language", "en-US")
    replace = bool(md.get("replace_existing"))
    action = settings.get("library", {}).get("action", "move")

    by_id = {it.file.id: it for it in items}
    final: dict[str, Path] = {o.file_id: Path(o.dst) for o in plan.ops if o.kind == "video"}
    for s in plan.skipped:
        if s.get("reason") == "Already named correctly" and s["file_id"] in by_id:
            final[s["file_id"]] = Path(by_id[s["file_id"]].file.path)
    if not final:
        return 0

    # fetch details once per film / show / season, a few at a time
    movies, shows, seasons = {}, {}, {}
    for fid in final:
        m = by_id[fid].match
        if m.kind == "movie":
            movies[m.tmdb_id] = None
        else:
            shows[m.tmdb_id] = None
            if m.season is not None:
                seasons[(m.tmdb_id, m.season)] = None

    def fetch(job):
        kind, key = job
        try:
            if kind == "movie":
                return job, movie_info(client.movie_details(key), region, language)
            if kind == "show":
                return job, show_info(client.tv_details(key), region, language)
            return job, client.season(*key)
        except Exception as exc:            # one failed lookup only skips that file's metadata
            log.warning("Couldn't fetch details for %s %s: %s", kind, key, exc)
            return job, None

    jobs = [("movie", k) for k in movies] + [("show", k) for k in shows] + [("season", k) for k in seasons]
    if progress:
        progress(f"Fetching details for {len(jobs)} title{'s' if len(jobs) != 1 else ''}…")
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (kind, key), val in pool.map(fetch, jobs):
            {"movie": movies, "show": shows, "season": seasons}[kind][key] = val

    taken = {os.path.normcase(o.dst) for o in plan.ops}
    moving_out = {os.path.normcase(o.src) for o in plan.ops}
    parents: dict[str, int] = {}
    for fid, dst in final.items():
        parents[os.path.normcase(str(dst.parent))] = parents.get(os.path.normcase(str(dst.parent)), 0) + 1
    n = len(plan.ops)
    files_with = set()

    def add(fid_list, kind, dst: Path, payload: dict):
        nonlocal n
        key = os.path.normcase(str(dst))
        if kind == "tags":
            key = "tags:" + key                 # tags go into the video itself, so it's meant to exist
        if key in taken:
            return False
        if kind == "meta" and exists(str(dst)) and not replace:
            plan.problems.append({"file_id": fid_list[0], "level": "info",
                                  "message": f"Kept the {LABELS.get(payload['what'], 'file')} already there ({dst.name})."})
            taken.add(key)
            return False
        taken.add(key)
        n += 1
        plan.ops.append(Op(id=f"m{n}", file_id=fid_list[0], src="", dst=str(dst),
                           action="tag" if kind == "tags" else "create", kind=kind,
                           payload={**payload, "file_ids": list(fid_list), "replace": replace}))
        files_with.add(fid_list[0])
        return True

    def alone(dst: Path) -> bool:
        if parents.get(os.path.normcase(str(dst.parent)), 0) > 1:
            return False
        try:
            for f in dst.parent.iterdir():
                if f.suffix.lower() in VIDEO_EXTS and os.path.normcase(str(f)) not in moving_out \
                        and os.path.normcase(str(f)) != os.path.normcase(str(dst)):
                    return False
        except OSError:
            pass
        return True

    def want_tags(fid, dst: Path) -> bool:
        if not (want["tags"] or want["cover"]):
            return False
        if action in ("hardlink", "symlink"):
            plan.problems.append({"file_id": fid, "level": "info",
                                  "message": "Tags weren't written: hardlinked and symlinked files share their contents with the original."})
            return False
        if dst.suffix.lower() not in (".mkv", ".mk3d", ".webm", ".mp4", ".m4v", ".mov"):
            plan.problems.append({"file_id": fid, "level": "info",
                                  "message": f"Tags can only go into MKV and MP4 files, not {dst.suffix}."})
            return False
        return True

    tags_payload = lambda tags, cover: {"what": "tags", "tags": tags if want["tags"] else None,
                                        "write_tags": want["tags"], "url": cover if want["cover"] else "",
                                        "safe_copy": bool(md.get("max_safety"))}

    show_done = set()
    for fid, dst in final.items():
        it = by_id[fid]
        m = it.match
        if m.kind == "movie":
            info = movies.get(m.tmdb_id)
            if not info:
                plan.problems.append({"file_id": fid, "level": "info", "message": "Metadata skipped: TheMovieDB couldn't be reached."})
                continue
            targets = movie_targets(dst, server, own_folder(dst, info["title"], alone(dst)), info)
            if want["poster"] and "poster" in targets:
                add([fid], "meta", targets["poster"][0], {"what": "poster", "url": targets["poster"][1]})
            if want["logo"] and "logo" in targets:
                add([fid], "meta", targets["logo"][0], {"what": "logo", "url": targets["logo"][1]})
            if want["nfo"]:
                add([fid], "meta", targets["nfo"][0], {"what": "nfo", "text": movie_nfo(info)})
            if want_tags(fid, dst):
                add([fid], "tags", dst, tags_payload(movie_tags(info), info.get("poster")))
            continue

        show = shows.get(m.tmdb_id)
        season = seasons.get((m.tmdb_id, m.season)) if m.season is not None else None
        if not show:
            plan.problems.append({"file_id": fid, "level": "info", "message": "Metadata skipped: TheMovieDB couldn't be reached."})
            continue
        eps = [e for e in (episode_info(season or {}, n_) for n_ in (m.episodes or [])) if e]
        show_dir, season_dir = show_dirs(dst, show["title"])
        group = [f for f, d in final.items() if by_id[f].match.kind != "movie" and by_id[f].match.tmdb_id == m.tmdb_id]
        if show_dir is not None and (m.tmdb_id, str(show_dir)) not in show_done:
            show_done.add((m.tmdb_id, str(show_dir)))
            if want["poster"] and show.get("poster"):
                add(group, "meta", show_dir / f"poster{_ext(show['poster'])}", {"what": "show_poster", "url": show["poster"]})
            if want["logo"] and show.get("logo"):
                add(group, "meta", show_dir / ("clearlogo.png" if server == "kodi" else "logo.png"),
                    {"what": "show_logo", "url": show["logo"]})
            if want["nfo"]:
                add(group, "meta", show_dir / "tvshow.nfo", {"what": "tvshow_nfo", "text": tvshow_nfo(show)})
        if want["season_posters"] and m.season is not None:
            url = (season or {}).get("poster_path")
            url = IMG + url if url else show.get("season_posters", {}).get(m.season, "")
            target = season_poster_target(server, show_dir, season_dir, m.season, url) if url else None
            if target is not None:
                sgroup = [f for f in group if by_id[f].match.season == m.season]
                add(sgroup, "meta", target, {"what": "season_poster", "url": url})
        if want["episode_thumbs"] and eps and eps[0].get("thumb"):
            thumb = eps[0]["thumb"]
            name = f"{dst.stem}{_ext(thumb)}" if server == "plex" else f"{dst.stem}-thumb{_ext(thumb)}"
            add([fid], "meta", dst.parent / name, {"what": "episode_thumb", "url": thumb})
        if want["nfo"] and eps:
            add([fid], "meta", dst.with_suffix(".nfo"), {"what": "nfo", "text": episode_nfo(show, eps)})
        if want_tags(fid, dst) and eps:
            add([fid], "tags", dst, tags_payload(episode_tags(show, eps), show.get("poster")))
    return len(files_with)


# --------------------------------------------------------------------------- staging

def _image_ok(path: Path) -> bool:
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return False
    return path.stat().st_size > 512 and (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prefetch(plan, client, folder: Path, progress=None) -> None:
    """Download images and write .nfo files to a staging folder before the batch runs."""
    folder.mkdir(parents=True, exist_ok=True)
    ops = [o for o in plan.ops if o.kind in ("meta", "tags")]
    urls = sorted({o.payload["url"] for o in ops if o.payload.get("url")})
    got: dict[str, Path | None] = {}

    def fetch(url):
        dest = folder / (hashlib.sha1(url.encode()).hexdigest() + _ext(url))
        if dest.exists() and _image_ok(dest):
            return url, dest
        try:
            client.download(url, dest)
            if _image_ok(dest):
                return url, dest
            log.warning("Downloaded file isn't a picture: %s", url)
        except Exception as exc:
            log.warning("Couldn't download %s: %s", url, exc)
        try:
            dest.unlink()
        except OSError:
            pass
        return url, None

    if urls and progress:
        progress(f"Downloading {len(urls)} image{'s' if len(urls) != 1 else ''}…")
    with ThreadPoolExecutor(max_workers=6) as pool:
        for url, dest in pool.map(fetch, urls):
            got[url] = dest

    for o in ops:
        p = o.payload
        if o.kind == "meta" and p.get("text") is not None:
            dest = folder / f"{o.id}.nfo"
            dest.write_text(p["text"], encoding="utf-8")
            p.pop("text")
            p["staged"] = str(dest)
        elif p.get("url"):
            dest = got.get(p["url"])
            if dest is None:
                if o.kind == "tags" and p.get("write_tags"):
                    p["url"] = ""                       # still write the tags, just without a cover
                    plan.problems.append({"file_id": o.file_id, "level": "info",
                                          "message": "The cover couldn't be downloaded, so only the tags were written."})
                    continue
                o.state, o.code, o.error = "skipped", "RNX-M009", "The image couldn't be downloaded."
                plan.problems.append({"file_id": o.file_id, "level": "info",
                                      "message": f"{LABELS.get(p['what'], 'Image')} skipped: it couldn't be downloaded."})
                continue
            if o.kind == "tags":
                p["cover"] = str(dest)
            else:
                p["staged"] = str(dest)
        if p.get("staged"):
            p["size"] = os.path.getsize(p["staged"])
            p["sha"] = _sha(Path(p["staged"]))


# --------------------------------------------------------------------------- placing and removing files

def place(op, backup_dir: Path) -> None:
    """Put a staged file at its destination without ever overwriting by accident."""
    p = op.payload
    dst = Path(op.dst)
    staged = Path(p["staged"])
    if not staged.exists() or _sha(staged) != p["sha"]:
        raise OSError(2, "The prepared file went missing")
    created = []
    parent = dst.parent
    missing = []
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for d in reversed(missing):
        d.mkdir(exist_ok=True)
        created.append(str(d))
    op.created_dirs = created
    if dst.exists():
        if not p.get("replace"):
            raise FileExistsError(str(dst))
        backup_dir.mkdir(parents=True, exist_ok=True)
        old = backup_dir / f"{op.id}{dst.suffix}.old"
        shutil.copy2(dst, old)
        p["replaced"] = str(old)
    part = dst.with_name(dst.name + ".rennix-part")
    shutil.copyfile(staged, part)
    with open(part, "rb+") as fh:
        os.fsync(fh.fileno())
    if _sha(part) != p["sha"]:
        part.unlink()
        raise OSError(5, "The saved file didn't match")
    if p.get("replaced"):
        os.replace(part, dst)
    elif os.name == "nt":
        os.rename(part, dst)                  # fails rather than overwrite
    else:
        try:
            os.link(part, dst)
            os.unlink(part)
        except FileExistsError:
            part.unlink()
            raise
        except OSError:
            if dst.exists():
                part.unlink()
                raise FileExistsError(str(dst))
            os.rename(part, dst)


def is_ours(op) -> bool:
    try:
        dst = Path(op["dst"])
        return dst.exists() and dst.stat().st_size == op["payload"].get("size") and _sha(dst) == op["payload"].get("sha")
    except (OSError, KeyError):
        return False


def remove(op: dict) -> None:
    """Undo for a placed file: delete it only if it's still exactly what Rennix saved."""
    dst = Path(op["dst"])
    old = op["payload"].get("replaced")
    if dst.exists():
        if not is_ours(op):
            raise FileExistsError(f"{dst.name} was changed after Rennix saved it, so it was kept")
        dst.unlink()
    if old and Path(old).exists():
        shutil.move(old, dst)
    for d in reversed(op.get("created_dirs") or []):
        try:
            Path(d).rmdir()
        except OSError:
            pass


def cleanup_part(op: dict) -> None:
    part = Path(op["dst"] + ".rennix-part")
    if part.exists():
        try:
            part.unlink()
        except OSError:
            pass
