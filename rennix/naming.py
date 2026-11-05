"""Naming formats: templates, the custom format language, and safe file names.

Text form of a format (what power users see in "Text" mode):

    {title} ({year})/{title} ({year})[ - {edition}]

* ``{token}`` or ``{token:option,option}`` is filled from the file's facts.
* ``[ ... ]`` is an optional part: it disappears, brackets and all, when any
  token inside it is empty. So ``[ - {edition}]`` only appears on files that
  have an edition.
* ``\\{ \\} \\[ \\] \\\\`` are literal characters (for Plex's ``{tmdb-603}``).
* ``/`` starts a new folder.

The visual editor in the app works on the same format as a list of parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- tokens

TOKENS: list[dict] = [
    # name, label, kinds, help (tooltip), example
    {"name": "title", "label": "Title", "kinds": "movie", "help": "🎬 The film's title from TheMovieDB.", "example": "The Matrix"},
    {"name": "year", "label": "Year", "kinds": "movie", "help": "📅 Release year.", "example": "1999"},
    {"name": "original_title", "label": "Original title", "kinds": "movie", "help": "🌍 Title in its original language.", "example": "Le Fabuleux Destin d'Amélie Poulain"},
    {"name": "collection", "label": "Collection", "kinds": "movie", "help": "📚 The film series it belongs to, if any.", "example": "The Matrix Collection"},
    {"name": "edition", "label": "Edition", "kinds": "movie", "help": "✂️ Extended, Director's Cut and so on, read from the original name.", "example": "Director's Cut"},
    {"name": "part", "label": "Part", "kinds": "movie", "help": "💿 Part number for films split into pieces (CD1, CD2).", "example": "1"},
    {"name": "show", "label": "Show title", "kinds": "episode", "help": "📺 The show's name from TheMovieDB.", "example": "Breaking Bad"},
    {"name": "show_year", "label": "Show year", "kinds": "episode", "help": "📅 Year the show started. Keeps remakes apart.", "example": "2008"},
    {"name": "season", "label": "Season", "kinds": "episode", "help": "🔢 Season number. Option 00 gives 02 instead of 2.", "example": "2"},
    {"name": "episode", "label": "Episode", "kinds": "episode", "help": "🔢 Episode number. Option 00 gives 05 instead of 5.", "example": "5"},
    {"name": "s00e00", "label": "Episode number", "kinds": "episode", "help": "🔢 Season and episode together, like S02E05. Pick a style: S02E05, 2x05, S2E5 or Season 2 Episode 5.", "example": "S02E05"},
    {"name": "episode_title", "label": "Episode title", "kinds": "episode", "help": "📝 The episode's name. Double episodes are joined with &.", "example": "Breakage"},
    {"name": "air_date", "label": "Air date", "kinds": "episode", "help": "🗓️ When the episode first aired, as 2026-03-14.", "example": "2009-04-12"},
    {"name": "absolute", "label": "Absolute number", "kinds": "episode", "help": "🔢 Episode count across all seasons, used for anime.", "example": "147"},
    {"name": "tmdb_id", "label": "TMDb ID", "kinds": "both", "help": "🔑 TheMovieDB's ID. Plex and Jellyfin use it for a perfect match.", "example": "603"},
    {"name": "imdb_id", "label": "IMDb ID", "kinds": "both", "help": "🔑 IMDb's ID, like tt0133093.", "example": "tt0133093"},
    {"name": "resolution", "label": "Resolution", "kinds": "both", "help": "🖥️ 2160p, 1080p, 720p…", "example": "1080p"},
    {"name": "source", "label": "Source", "kinds": "both", "help": "💽 BluRay, WEB-DL, HDTV…", "example": "BluRay"},
    {"name": "video_codec", "label": "Video codec", "kinds": "both", "help": "🎞️ H.264, HEVC, AV1…", "example": "HEVC"},
    {"name": "hdr", "label": "HDR", "kinds": "both", "help": "🌈 HDR10, HDR10+, Dolby Vision. Empty for normal video.", "example": "HDR10"},
    {"name": "audio", "label": "Audio", "kinds": "both", "help": "🔊 Atmos, DTS, 5.1…", "example": "Atmos 7.1"},
    {"name": "languages", "label": "Language", "kinds": "both", "help": "🗣️ Audio languages, like ENG, FRE.", "example": "ENG"},
    {"name": "subtitle_languages", "label": "Subtitle languages", "kinds": "both", "help": "💬 Built-in subtitle languages.", "example": "ENG, FRE"},
    {"name": "release_group", "label": "Release group", "kinds": "both", "help": "🏷️ Group name from the original file name.", "example": "SPARKS"},
]
TOKEN_NAMES = {t["name"] for t in TOKENS}
ALIASES = {"n": "title", "y": "year", "s": "season", "e": "episode", "t": "episode_title",
           "name": "title", "series": "show", "sxe": "s00e00", "ep_title": "episode_title"}
OPTIONS = {"00", "0", "000", "x", "short", "long", "upper", "lower", "title", "the",
           "dots", "first"}

PRESETS: dict[str, dict[str, str]] = {
    "Plex": {
        "movie": r"{title} ({year}) \{tmdb-{tmdb_id}\}/{title} ({year})[ \{edition-{edition}\}][ - pt{part}]",
        "episode": r"{show} ({show_year})/Season {season:00}/{show} ({show_year}) - {s00e00}[ - {episode_title}]",
        "description": "Plex's recommended layout, with the TMDb ID for a perfect match.",
    },
    "Jellyfin": {
        "movie": r"{title} ({year}) \[tmdbid-{tmdb_id}\]/{title} ({year})[ - {edition}][ - part{part}]",
        "episode": r"{show} ({show_year}) \[tmdbid-{tmdb_id}\]/Season {season:00}/{show} ({show_year}) - {s00e00}[ - {episode_title}]",
        "description": "Jellyfin's layout, with its TMDb ID tag.",
    },
    "Emby": {
        "movie": r"{title} ({year}) \[tmdbid={tmdb_id}\]/{title} ({year})[ - {edition}][ - part{part}]",
        "episode": r"{show} ({show_year}) \[tmdbid={tmdb_id}\]/Season {season:00}/{show} - {s00e00}[ - {episode_title}]",
        "description": "Emby's layout, with its TMDb ID tag.",
    },
    "Kodi": {
        "movie": r"{title} ({year})/{title} ({year})[ - {edition}][ - part{part}]",
        "episode": r"{show}/Season {season}/{show} {s00e00}[ - {episode_title}]",
        "description": "Kodi's layout.",
    },
    "Simple": {
        "movie": r"{title} ({year})",
        "episode": r"{show} - {s00e00:x}[ - {episode_title}]",
        "description": "Just the file name, no folders.",
    },
    "Detailed": {
        "movie": r"{title} ({year})[ - {edition}][ \[{resolution}[ {source}][ {hdr}]\]]",
        "episode": r"{show} - {s00e00}[ - {episode_title}][ \[{resolution}\]]",
        "description": "Adds quality details in brackets.",
    },
}

SAMPLE_FACTS = {
    "movie": {"title": "The Matrix", "year": 1999, "original_title": "The Matrix",
              "collection": "The Matrix Collection", "edition": "", "part": None,
              "tmdb_id": 603, "imdb_id": "tt0133093", "resolution": "1080p", "source": "BluRay",
              "video_codec": "H.264", "hdr": "", "audio": "DTS 5.1", "languages": "ENG",
              "subtitle_languages": "", "release_group": "GROUP"},
    "episode": {"show": "Breaking Bad", "show_year": 2008, "season": 2, "episodes": [5],
                "episode_titles": ["Breakage"], "air_date": "2009-04-05", "absolute": None,
                "tmdb_id": 1396, "imdb_id": "tt0903747", "resolution": "720p", "source": "HDTV",
                "video_codec": "H.264", "hdr": "", "audio": "", "languages": "ENG",
                "subtitle_languages": "", "release_group": "CTU"},
}


class FormatError(ValueError):
    pass


# --------------------------------------------------------------------------- parsing

@dataclass
class Text:
    value: str

    def to_json(self) -> dict:
        return {"t": "text", "v": self.value}


@dataclass
class Token:
    name: str
    opts: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {"t": "token", "name": self.name, "opts": list(self.opts)}


@dataclass
class Group:
    parts: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {"t": "group", "parts": [p.to_json() for p in self.parts]}


def parse_format(text: str) -> list:
    """Turn the text form into parts. Raises FormatError with a friendly message."""
    pos = 0
    stack: list[list] = [[]]
    buf = ""

    def flush():
        nonlocal buf
        if buf:
            stack[-1].append(Text(buf))
            buf = ""

    while pos < len(text):
        ch = text[pos]
        if ch == "\\" and pos + 1 < len(text):
            buf += text[pos + 1]
            pos += 2
            continue
        if ch == "{":
            end = text.find("}", pos)
            if end == -1:
                raise FormatError("A { is never closed. Add a } after the part's name.")
            inner = text[pos + 1:end].strip()
            name, _, opts = inner.partition(":")
            name = ALIASES.get(name.strip().lower(), name.strip().lower())
            if not name:
                raise FormatError("Empty {} found. Put a part name inside, like {title}.")
            flush()
            stack[-1].append(Token(name, [o.strip().lower() for o in opts.split(",") if o.strip()]))
            pos = end + 1
            continue
        if ch == "}":
            raise FormatError("A } appears without a matching {. Use \\} for a literal }.")
        if ch == "[":
            flush()
            stack.append([])
            pos += 1
            continue
        if ch == "]":
            if len(stack) == 1:
                raise FormatError("A ] appears without a matching [. Use \\] for a literal ].")
            flush()
            inner = stack.pop()
            stack[-1].append(Group(inner))
            pos += 1
            continue
        buf += ch
        pos += 1
    flush()
    if len(stack) != 1:
        raise FormatError("An optional part [ is never closed. Add a ] at its end.")
    return stack[0]


def _escape(s: str) -> str:
    return re.sub(r"([\\{}\[\]])", r"\\\1", s)


def format_to_text(parts: list) -> str:
    out = ""
    for p in parts:
        if isinstance(p, Text):
            out += _escape(p.value)
        elif isinstance(p, Token):
            out += "{" + p.name + (":" + ",".join(p.opts) if p.opts else "") + "}"
        elif isinstance(p, Group):
            out += "[" + format_to_text(p.parts) + "]"
    return out


def parts_from_json(items: list) -> list:
    out = []
    for it in items:
        t = it.get("t")
        if t == "text":
            out.append(Text(str(it.get("v", ""))))
        elif t == "token":
            out.append(Token(str(it.get("name", "")), list(it.get("opts") or [])))
        elif t == "group":
            out.append(Group(parts_from_json(it.get("parts") or [])))
    return out


def parts_to_json(parts: list) -> list:
    return [p.to_json() for p in parts]


def tokens_in(parts: list) -> set:
    found = set()
    for p in parts:
        if isinstance(p, Token):
            found.add(p.name)
        elif isinstance(p, Group):
            found |= tokens_in(p.parts)
    return found


def validate(text: str, kind: str) -> list[dict]:
    """Problems with a format, as [{'level': 'error'|'warning', 'message': ...}]."""
    issues: list[dict] = []
    try:
        parts = parse_format(text)
    except FormatError as exc:
        return [{"level": "error", "message": str(exc)}]
    used = tokens_in(parts)
    for name in sorted(used - TOKEN_NAMES):
        issues.append({"level": "error", "message": f"Unknown part {{{name}}}."})

    def check_opts(ps):
        for p in ps:
            if isinstance(p, Token):
                for o in p.opts:
                    if o not in OPTIONS and not re.fullmatch(r"max\d+", o):
                        issues.append({"level": "warning",
                                       "message": f"Option '{o}' on {{{p.name}}} isn't recognised and is ignored."})
            elif isinstance(p, Group):
                check_opts(p.parts)

    check_opts(parts)
    if kind == "episode":
        has_ep = "s00e00" in used or ("episode" in used and "season" in used) \
            or "air_date" in used or "absolute" in used
        if not has_ep:
            issues.append({"level": "error",
                           "message": "TV formats need the episode number ({s00e00}, or {season} and {episode}), "
                                      "otherwise episodes would get the same name."})
        movie_only = sorted(used & {"title", "year"})
        if movie_only:
            issues.append({"level": "warning",
                           "message": "{title} and {year} are for films. For shows use {show} and {show_year}."})
    else:
        if "title" not in used:
            issues.append({"level": "error", "message": "Film formats need {title}."})
        elif "year" not in used:
            issues.append({"level": "warning",
                           "message": "Without {year}, remakes like Dune (1984) and Dune (2021) would clash."})
    if re.search(r"(^|/)\s*(/|$)", text):
        issues.append({"level": "error", "message": "There's an empty folder name (two / in a row)."})
    return issues


# --------------------------------------------------------------------------- facts

def _upper_langs(langs) -> str:
    if not langs:
        return ""
    codes = {"en": "ENG", "fr": "FRE", "de": "GER", "es": "SPA", "it": "ITA", "ja": "JPN",
             "ko": "KOR", "ru": "RUS", "hi": "HIN", "nl": "DUT", "sv": "SWE", "pt": "POR",
             "pl": "POL", "zh": "CHI"}
    return ", ".join(codes.get(l, l.upper()) for l in langs)


def facts_for(identified: Any) -> dict:
    """Everything a format can use, from an identified file."""
    m = identified.match
    p = identified.parsed
    probe = identified.probe or {}
    hdr = probe.get("hdr") or p.hdr
    audio_bits = []
    if probe.get("audio"):
        audio_bits.append(str(probe["audio"]))
        if probe.get("channels"):
            audio_bits.append(probe["channels"])
    else:
        audio_bits = list(p.audio)
    facts = {
        "resolution": probe.get("resolution") or p.resolution,
        "source": p.source,
        "video_codec": probe.get("video_codec") or p.video_codec,
        "hdr": " ".join(hdr) if isinstance(hdr, list) else (hdr or ""),
        "audio": " ".join(audio_bits),
        "languages": _upper_langs(probe.get("audio_languages") or p.languages),
        "subtitle_languages": _upper_langs(probe.get("subtitle_languages")),
        "release_group": p.release_group,
        "imdb_id": m.imdb_id if m else "",
        "tmdb_id": m.tmdb_id if m else "",
    }
    if m is None:
        return facts
    if m.kind == "movie":
        facts.update(title=m.title, year=m.year, original_title=m.original_title,
                     collection=m.collection, edition=p.edition, part=p.part)
    else:
        facts.update(show=m.title, show_year=m.year, season=m.season, episodes=m.episodes,
                     episode_titles=m.episode_titles, air_date=m.air_date,
                     absolute=m.absolute or p.absolute)
    return facts


# --------------------------------------------------------------------------- rendering

_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)


def clean_value(value: str) -> str:
    """Make one fact safe inside a file name (no folder breaks hidden in titles)."""
    v = str(value)
    v = v.replace("/", "-").replace("\\", "-")
    v = re.sub(r"\s*:\s+", " - ", v)       # Dune: Part Two -> Dune - Part Two
    v = v.replace(":", "-")                 # 10:30 -> 10-30
    v = v.replace('"', "'")
    v = re.sub(r'[*?<>|]', "", v)
    v = re.sub(r"[\x00-\x1f]", "", v)
    return re.sub(r"\s+", " ", v).strip()


def clean_segment(seg: str) -> str:
    """Make one folder or file name valid on Windows, Mac and Linux."""
    s = re.sub(r'[\x00-\x1f]', "", seg)
    s = re.sub(r"\s*:\s+", " - ", s).replace(":", "-")
    s = s.replace('"', "'")
    s = re.sub(r'[*?<>|\\]', "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ").lstrip(" ")
    if _RESERVED.match(s):
        s += "_"
    return s


def _pad(n: Any, opts: list) -> str:
    if n is None or n == "":
        return ""
    width = 0
    for o in opts:
        if re.fullmatch(r"0+", o):
            width = len(o)
    try:
        return str(int(n)).zfill(width)
    except (TypeError, ValueError):
        return str(n)


def _episode_code(facts: dict, opts: list) -> str:
    season = facts.get("season")
    eps = facts.get("episodes") or []
    if season is None or not eps:
        if facts.get("absolute"):
            return str(facts["absolute"]).zfill(2)
        return ""
    first, last = eps[0], eps[-1]
    multi = len(eps) > 1
    if "x" in opts:
        return f"{season}x{first:02d}" + (f"-{last:02d}" if multi else "")
    if "short" in opts:
        return f"S{season}E{first}" + (f"-E{last}" if multi else "")
    if "long" in opts:
        return f"Season {season} Episode {first}" + (f"-{last}" if multi else "")
    return f"S{season:02d}E{first:02d}" + (f"-E{last:02d}" if multi else "")


def _token_value(tok: Token, facts: dict) -> str:
    name, opts = tok.name, tok.opts
    if name == "s00e00":
        val = _episode_code(facts, opts)
    elif name == "episode":
        eps = facts.get("episodes") or []
        val = _pad(eps[0], opts) + ("-" + _pad(eps[-1], opts) if len(eps) > 1 else "") if eps else ""
    elif name == "episode_title":
        titles = [t for t in (facts.get("episode_titles") or []) if t]
        # don't repeat "Part 1 & Part 2" style duplicates
        val = " & ".join(dict.fromkeys(titles))
    elif name in ("season", "absolute", "part"):
        val = _pad(facts.get(name), opts)
    else:
        raw = facts.get(name)
        val = "" if raw is None else str(raw)
    if not val:
        return ""
    val = clean_value(val)
    if "the" in opts:
        m = re.match(r"^(the|a|an) (.+)$", val, re.I)
        if m:
            val = f"{m.group(2)}, {m.group(1)}"
    if "upper" in opts:
        val = val.upper()
    elif "lower" in opts:
        val = val.lower()
    elif "title" in opts:
        val = val.title()
    if "first" in opts:
        letter = re.sub(r"^(the|a|an) ", "", val, flags=re.I)[:1].upper()
        val = letter if letter.isalpha() else "#"
    for o in opts:
        m = re.fullmatch(r"max(\d+)", o)
        if m:
            val = val[: int(m.group(1))].rstrip(" -.,")
    if "dots" in opts:
        val = re.sub(r"\s+", ".", val)
    return val


def _render(parts: list, facts: dict) -> tuple[str, bool]:
    """Returns (text, every_token_filled)."""
    out, complete = "", True
    for p in parts:
        if isinstance(p, Text):
            out += p.value
        elif isinstance(p, Token):
            v = _token_value(p, facts)
            if not v:
                complete = False
            out += v
        elif isinstance(p, Group):
            inner, ok = _render(p.parts, facts)
            if ok:
                out += inner
    return out, complete


def _tidy(path: str) -> str:
    segs = []
    for seg in path.split("/"):
        s = re.sub(r"\s+", " ", seg)
        s = re.sub(r"\(\s*\)|\[\s*\]|\{\s*\}", "", s)       # empty brackets
        s = re.sub(r"(\s-)+\s*-\s", " - ", s)                # " - - " -> " - "
        s = re.sub(r"\s+", " ", s).strip(" -_.,")
        segs.append(s)
    return "/".join(segs)


def render(fmt: str | list, facts: dict, ext: str = "", max_path: int | None = None,
           base_len: int = 0) -> str:
    """Build the relative path (folders/file name + extension) for one file.

    ``max_path`` is the total limit (e.g. 259 on Windows); ``base_len`` is the
    length of the folder the result will sit in. Episode titles shrink first.
    """
    parts = parse_format(fmt) if isinstance(fmt, str) else fmt
    text, _ = _render(parts, facts)
    rel = "/".join(clean_segment(s) for s in _tidy(text).split("/") if clean_segment(s))
    if not rel:
        return ""
    full = rel + ext
    if max_path and base_len + 1 + len(full) > max_path:
        titles = facts.get("episode_titles") or []
        joined = " & ".join(t for t in titles if t)
        if joined:
            for cut in (60, 40, 25, 12, 0):
                f2 = dict(facts)
                f2["episode_titles"] = [joined[:cut].rstrip(" -.,&")] if cut else []
                text, _ = _render(parts, f2)
                rel = "/".join(clean_segment(s) for s in _tidy(text).split("/") if clean_segment(s))
                full = rel + ext
                if base_len + 1 + len(full) <= max_path:
                    break
        if base_len + 1 + len(full) > max_path:
            folders, _, name = rel.rpartition("/")
            room = max_path - base_len - 1 - len(ext) - (len(folders) + 1 if folders else 0)
            name = name[: max(room, 8)].rstrip(" -.,")
            rel = f"{folders}/{name}" if folders else name
            full = rel + ext
    return full


def preview(fmt: str, kind: str, facts: dict | None = None, ext: str = ".mkv") -> str:
    return render(fmt, facts or SAMPLE_FACTS[kind], ext)


# --------------------------------------------------------------------------- saved formats

class Formats:
    """Built-in templates plus the user's saved formats (formats.json)."""

    def __init__(self, path=None):
        self.path = path
        self.custom: dict[str, dict] = {}
        if path is not None and path.exists():
            import json
            try:
                self.custom = json.loads(path.read_text(encoding="utf-8")).get("formats", {})
            except (OSError, ValueError):
                self.custom = {}

    def all(self) -> dict:
        out = {name: dict(v, builtin=True) for name, v in PRESETS.items()}
        for name, v in self.custom.items():
            out[name] = dict(v, builtin=False)
        return out

    def get(self, name: str, kind: str) -> str:
        fmt = self.custom.get(name) or PRESETS.get(name) or PRESETS["Plex"]
        return fmt.get(kind) or PRESETS["Plex"][kind]

    def save(self, name: str, movie: str | None = None, episode: str | None = None,
             description: str = "") -> None:
        if name in PRESETS:
            raise FormatError(f"'{name}' is a built-in template. Save your version under another name.")
        cur = self.custom.get(name, {})
        if movie is not None:
            cur["movie"] = movie
        if episode is not None:
            cur["episode"] = episode
        cur["description"] = description or cur.get("description", "Your own format")
        for kind in ("movie", "episode"):
            cur.setdefault(kind, PRESETS["Plex"][kind])
        self.custom[name] = cur
        self._write()

    def delete(self, name: str) -> None:
        self.custom.pop(name, None)
        self._write()

    def _write(self) -> None:
        if self.path is None:
            return
        from .config import atomic_write_json
        atomic_write_json(self.path, {"formats": self.custom})
