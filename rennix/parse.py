"""Read a messy file name and pull out what it says.

``The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv`` -> movie, "The Matrix", 1999, 1080p ...

Uses the guessit library when it's installed (it's in requirements.txt) and
always runs Rennix's own parser too, which adds things guessit doesn't do:
IDs in names, country codes, and alternative readings of ambiguous names.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field, asdict
from pathlib import PurePath

try:
    from guessit import guessit as _guessit
except ImportError:
    _guessit = None

CURRENT_YEAR = _dt.date.today().year

# --------------------------------------------------------------------------- vocab

RESOLUTIONS = {
    "2160p": "2160p", "4k": "2160p", "uhd": "2160p", "1440p": "1440p",
    "1080p": "1080p", "1080i": "1080i", "720p": "720p", "576p": "576p",
    "480p": "480p", "360p": "360p",
}
SOURCES = {
    "bluray": "BluRay", "blu-ray": "BluRay", "bdrip": "BluRay", "brrip": "BluRay",
    "bdremux": "Remux", "remux": "Remux", "web-dl": "WEB-DL", "webdl": "WEB-DL",
    "webrip": "WEBRip", "web": "WEB", "hdtv": "HDTV", "pdtv": "HDTV", "dvdrip": "DVD",
    "dvd": "DVD", "dvdscr": "DVD", "hdrip": "HDRip", "hdcam": "CAM", "cam": "CAM",
    "amzn": "WEB", "nf": "WEB", "dsnp": "WEB", "hmax": "WEB", "atvp": "WEB",
}
CODECS = {
    "x264": "H.264", "h264": "H.264", "avc": "H.264", "x265": "HEVC", "h265": "HEVC",
    "hevc": "HEVC", "av1": "AV1", "xvid": "XviD", "divx": "DivX", "vp9": "VP9",
}
HDR = {"hdr": "HDR", "hdr10": "HDR10", "hdr10+": "HDR10+", "hdr10plus": "HDR10+",
       "dv": "Dolby Vision", "dovi": "Dolby Vision", "dolbyvision": "Dolby Vision"}
AUDIO = {
    "atmos": "Atmos", "truehd": "TrueHD", "dts": "DTS", "dts-hd": "DTS-HD",
    "dtshd": "DTS-HD", "dts-x": "DTS:X", "ddp": "DD+", "dd+": "DD+", "eac3": "DD+",
    "dd": "DD", "ac3": "DD", "aac": "AAC", "flac": "FLAC", "mp3": "MP3", "opus": "Opus",
}
EDITIONS = {
    "extended": "Extended", "unrated": "Unrated", "remastered": "Remastered",
    "theatrical": "Theatrical", "imax": "IMAX", "uncut": "Uncut", "uncensored": "Uncensored",
    "criterion": "Criterion",
}
OTHER = {"proper", "repack", "internal", "limited", "multi", "subbed", "dubbed",
         "dual", "complete", "hc", "10bit", "8bit", "hq", "rerip", "readnfo", "nfofix"}
LANGUAGES = {
    "english": "en", "eng": "en", "french": "fr", "fre": "fr", "fra": "fr", "vff": "fr",
    "truefrench": "fr", "german": "de", "ger": "de", "deu": "de", "spanish": "es",
    "spa": "es", "esp": "es", "italian": "it", "ita": "it", "japanese": "ja", "jpn": "ja",
    "korean": "ko", "kor": "ko", "russian": "ru", "rus": "ru", "hindi": "hi",
    "dutch": "nl", "swedish": "sv", "portuguese": "pt", "polish": "pl",
}
COUNTRIES = {"us": "US", "uk": "GB", "au": "AU", "nz": "NZ", "ca": "CA"}

GENERIC_NAMES = re.compile(
    r"^(movie|film|video|vid|clip|untitled|new|file|main|feature|title\d*|"
    r"vid[_ -]?\d+|img[_ -]?\d+|dsc[_ -]?\d+|mvi[_ -]?\d+|\d{1,6})$",
    re.I,
)

# --------------------------------------------------------------------------- patterns

_ID_PATTERNS = [
    ("tmdb", re.compile(r"[\{\[]\s*tmdb(?:id)?\s*[-=]\s*(\d+)\s*[\}\]]", re.I)),
    ("imdb", re.compile(r"[\{\[]\s*imdb(?:id)?\s*[-=]\s*(tt\d{7,9})\s*[\}\]]", re.I)),
    ("tvdb", re.compile(r"[\{\[]\s*tvdb(?:id)?\s*[-=]\s*(\d+)\s*[\}\]]", re.I)),
    ("imdb", re.compile(r"(?<![a-z0-9])(tt\d{7,9})(?![0-9])", re.I)),
]
_EDITION_PLEX = re.compile(r"\{edition-([^}]+)\}", re.I)
_LEADING_GROUP = re.compile(r"^\s*\[([^\]]+)\]\s*")
_CRC = re.compile(r"\[[0-9A-F]{8}\]", re.I)
_DATE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[ ._\-](0[1-9]|1[0-2])[ ._\-](0[1-9]|[12]\d|3[01])(?!\d)"
)
_H26X = re.compile(r"\b([HhXx])\.(26[45])\b")
_CHANNELS = re.compile(r"(?<![\d.])([1-7])\.([01])(?![\d])")
_SXE = re.compile(
    r"\bs(\d{1,2})\s?[ ._-]?\s?e(\d{1,4})((?:\s?[-+ ]?\s?e?\d{1,4}(?=\b))*)", re.I
)
_X = re.compile(r"(?<![\dx])(\d{1,2})x(\d{2,3})(?:-(\d{2,3}))?(?![\dp])", re.I)
_SEASON_EP_WORDS = re.compile(r"\bseason\s*(\d{1,2})\s*episode\s*(\d{1,4})\b", re.I)
_SEASON_ONLY = re.compile(r"^(?:season|series|staffel|saison|temporada)\s*(\d{1,2})$|^s(\d{1,2})$", re.I)
_SPECIALS_FOLDER = re.compile(r"^specials?$", re.I)
_EP_WORD = re.compile(r"\b(?:ep|episode|e)\.?\s?(\d{1,4})\b", re.I)
_DASH_NUMBER = re.compile(r"\s-\s(\d{1,4})(?:v\d)?(?:\s|$)")
_PART = re.compile(r"^(?:cd|part|pt|disc|disk|dvd)(\d{1,2})$", re.I)
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_RES_TOKEN = re.compile(r"^\d{3,4}[pi]$", re.I)


@dataclass
class Parsed:
    raw: str
    kind: str = "unknown"            # movie | episode | unknown
    title: str = ""
    alt_titles: list = field(default_factory=list)   # [(title, year)] alternatives to try
    year: int | None = None
    country: str | None = None
    season: int | None = None
    episodes: list = field(default_factory=list)
    absolute: int | None = None
    air_date: str | None = None
    episode_title: str = ""
    part: int | None = None
    resolution: str = ""
    source: str = ""
    video_codec: str = ""
    audio: list = field(default_factory=list)
    hdr: list = field(default_factory=list)
    edition: str = ""
    release_group: str = ""
    languages: list = field(default_factory=list)
    other: list = field(default_factory=list)
    ids: dict = field(default_factory=dict)
    is_anime: bool = False
    generic: bool = False            # name carries no real title (VID_00231)
    engine: str = "builtin"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_episode(self) -> bool:
        return self.kind == "episode"


# --------------------------------------------------------------------------- helpers

def _extract_ids(text: str, ids: dict) -> str:
    for name, pat in _ID_PATTERNS:
        for m in pat.finditer(text):
            ids.setdefault(name, m.group(1).lower() if name == "imdb" else int(m.group(1)))
        text = pat.sub(" ", text)
    return text


def _clean_title(tokens: list[str]) -> str:
    title = " ".join(tokens)
    title = re.sub(r"\s+", " ", title).strip(" -_")
    return title


def _tidy_case(title: str) -> str:
    if title.islower() or title.isupper():
        small = {"a", "an", "and", "the", "of", "in", "on", "at", "to", "for", "or", "vs"}
        words = title.lower().split()
        return " ".join(
            w if (i and w in small) else (w[:1].upper() + w[1:]) for i, w in enumerate(words)
        )
    return title


def _is_year(tok: str) -> bool:
    return bool(_YEAR.match(tok)) and 1900 <= int(tok) <= CURRENT_YEAR + 1


def _quality_kind(tok: str) -> str | None:
    t = tok.lower()
    if t in RESOLUTIONS or _RES_TOKEN.match(t):
        return "resolution"
    if t in SOURCES:
        return "source"
    if t in CODECS:
        return "codec"
    if t in HDR:
        return "hdr"
    if t in AUDIO or re.match(r"^(ddp|dd\+?|aac|dts|truehd|eac3|ac3|flac|atmos)?\d(ch)\d$", t):
        return "audio"
    if t in EDITIONS:
        return "edition"
    if t in OTHER:
        return "other"
    if t in LANGUAGES and t not in {"dutch"}:
        return "language"
    if _PART.match(t):
        return "part"
    return None


# --------------------------------------------------------------------------- builtin parser

def parse_builtin(name: str, is_file: bool = True) -> Parsed:
    """Rennix's own parser. Handles the common scene and personal naming styles."""
    stem = PurePath(name).name
    if is_file and re.search(r"\.(?=[a-z0-9]*[a-z])[a-z0-9]{2,4}$", stem, re.I):
        stem = stem.rsplit(".", 1)[0]
    p = Parsed(raw=name)

    text = stem
    text = _extract_ids(text, p.ids)
    m = _EDITION_PLEX.search(text)
    if m:
        p.edition = m.group(1).strip()
        text = _EDITION_PLEX.sub(" ", text)

    m = _LEADING_GROUP.match(text)
    if m:
        p.release_group = m.group(1).strip()
        p.is_anime = True  # [Group] Title - 01 is the fansub convention
        text = text[m.end():]
    text = _CRC.sub(" ", text)

    m = _DATE.search(text)
    if m:
        p.air_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        text = text[: m.start()] + " \x00DATE\x00 " + text[m.end():]

    text = _H26X.sub(lambda mm: mm.group(1) + mm.group(2), text)
    text = _CHANNELS.sub(lambda mm: f"{mm.group(1)}ch{mm.group(2)}", text)

    # release group: trailing -GROUP (only when there's no space in it)
    m = re.search(r"-([A-Za-z0-9]{2,20})(?:\[[^\]]*\])?$", text.strip())
    if m and not re.search(r"\s-\s", text.strip()[-(len(m.group(0)) + 2):]):
        before = text.strip()[: -len(m.group(0))]
        last = re.split(r"[ ._]", before)[-1].lower() if before else ""
        if _quality_kind(last) or last in OTHER or re.match(r"^\d{3,4}p$", last):
            p.release_group = p.release_group or m.group(1)
            text = before

    # director's cut / final cut / special edition (multi-word editions)
    for pat, label in (
        (r"director'?s[ ._-]?cut|dc(?=[ ._])", "Director's Cut"),
        (r"final[ ._-]?cut", "Final Cut"),
        (r"special[ ._-]?edition", "Special Edition"),
        (r"ultimate[ ._-]?(?:cut|edition)", "Ultimate Edition"),
        (r"collector'?s[ ._-]?edition", "Collector's Edition"),
        (r"anniversary[ ._-]?edition", "Anniversary Edition"),
        (r"dolby[ ._-]?vision", None),
    ):
        mm = re.search(rf"(?<![a-z]){pat}(?![a-z])", text, re.I)
        if mm:
            if label:
                p.edition = p.edition or label
            else:
                p.hdr.append("Dolby Vision")
            text = text[: mm.start()] + " \x00Q\x00 " + text[mm.end():]

    # unify separators
    work = re.sub(r"[._\[\](){}]+", " ", text)
    work = re.sub(r"\s+", " ", work).strip()

    # ---- episode markers (searched in the whole string) -----------------
    ep_span = None
    m = _SXE.search(work)
    if m:
        p.season = int(m.group(1))
        p.episodes = [int(m.group(2))]
        extra = m.group(3) or ""
        nums = [int(n) for n in re.findall(r"\d{1,4}", extra)]
        if nums:
            if "-" in extra and len(nums) == 1 and nums[0] > p.episodes[0]:
                p.episodes = list(range(p.episodes[0], nums[0] + 1))
            else:
                p.episodes += [n for n in nums if n not in p.episodes]
        ep_span = m.span()
    else:
        m = _SEASON_EP_WORDS.search(work)
        if m:
            p.season, p.episodes = int(m.group(1)), [int(m.group(2))]
            ep_span = m.span()
        else:
            m = _X.search(work)
            if m and not _is_year(m.group(1) + m.group(2)):
                p.season, p.episodes = int(m.group(1)), [int(m.group(2))]
                if m.group(3):
                    p.episodes = list(range(int(m.group(2)), int(m.group(3)) + 1))
                ep_span = m.span()

    if ep_span is None and p.air_date is None:
        # anime / absolute numbering: "Title - 147" or "Title Ep 147"
        m = _DASH_NUMBER.search(" " + work + " ")
        mw = _EP_WORD.search(work)
        if m and not _is_year(m.group(1)):
            p.absolute = int(m.group(1))
            ep_span = (m.start() - 1, m.end() - 1)
        elif mw and not _is_year(mw.group(1)):
            p.absolute = int(mw.group(1))
            ep_span = mw.span()
        elif p.is_anime:
            mb = re.search(r"(?:^|\s)(\d{1,4})(?:v\d)?(?:\s|$)", work)
            if mb and not _is_year(mb.group(1)) and not _RES_TOKEN.match(mb.group(1) + "p"):
                p.absolute = int(mb.group(1))
                ep_span = mb.span()

    # ---- tokens ------------------------------------------------------------
    if ep_span:
        head, tail = work[: ep_span[0]], work[ep_span[1]:]
    else:
        head, tail = work, ""
    head_tokens = [t for t in head.split(" ") if t]
    tail_tokens = [t for t in tail.split(" ") if t]

    # title ends at the first marker in the head
    title_tokens: list[str] = []
    years: list[int] = []
    rest: list[str] = []
    stopped = False
    for i, tok in enumerate(head_tokens):
        if stopped:
            rest.append(tok)
            continue
        if tok in ("\x00DATE\x00", "\x00Q\x00") or "\x00" in tok:
            stopped = True
            continue
        if _is_year(tok):
            nxt = head_tokens[i + 1] if i + 1 < len(head_tokens) else ""
            if not title_tokens:
                title_tokens.append(tok)          # "1917 (2019)", "2001 A Space Odyssey"
                continue
            if _is_year(nxt):
                title_tokens.append(tok)          # "Wonder Woman 1984 2020"
                continue
            years.append(int(tok))
            stopped = True
            continue
        if _quality_kind(tok) and title_tokens:
            stopped = True
            rest.append(tok)
            continue
        if tok == "-" and title_tokens:
            # " - " usually separates title from extra info in personal names
            nxt = head_tokens[i + 1] if i + 1 < len(head_tokens) else ""
            if not nxt or _quality_kind(nxt) or _is_year(nxt):
                stopped = True
                continue
        title_tokens.append(tok)

    for tok in rest + tail_tokens:
        if _is_year(tok) and p.year is None and not years:
            years.append(int(tok))

    if years:
        p.year = years[0]
    # a lone number title such as "2012" with no other year
    if len(title_tokens) == 1 and _is_year(title_tokens[0]) and p.year is None and not tail_tokens:
        pass  # keep "2012" as the title; identification also tries it as a year

    # country suffix (The Office US)
    if len(title_tokens) > 1 and title_tokens[-1].lower() in COUNTRIES:
        p.country = COUNTRIES[title_tokens[-1].lower()]
        p.alt_titles.append((_clean_title(title_tokens), p.year))
        title_tokens = title_tokens[:-1]

    p.title = _tidy_case(_clean_title(title_tokens))

    # alternative readings
    if p.year and title_tokens and not ep_span:
        p.alt_titles.append((f"{p.title} {p.year}", None))   # "Wonder Woman 1984"
    if len(title_tokens) > 1 and _is_year(title_tokens[-1]) and p.year is None:
        p.alt_titles.append((_clean_title(title_tokens[:-1]), int(title_tokens[-1])))
    if len(title_tokens) == 1 and _is_year(title_tokens[0]):
        p.alt_titles.append((title_tokens[0], None))

    # episode title: words after the episode marker until the first quality/year token
    if ep_span and tail_tokens:
        words = []
        for tok in tail_tokens:
            if _quality_kind(tok) or _is_year(tok) or "\x00" in tok:
                break
            words.append(tok)
        p.episode_title = _tidy_case(_clean_title(words)).strip(" -")

    # quality and extras from all tokens
    for tok in head_tokens + tail_tokens:
        t = tok.lower()
        kind = _quality_kind(t)
        if kind == "resolution":
            p.resolution = p.resolution or RESOLUTIONS.get(t, t)
        elif kind == "source":
            p.source = p.source or SOURCES[t]
        elif kind == "codec":
            p.video_codec = p.video_codec or CODECS[t]
        elif kind == "hdr":
            if HDR[t] not in p.hdr:
                p.hdr.append(HDR[t])
        elif kind == "audio":
            label = AUDIO.get(t)
            if label is None:
                mm = re.match(r"^(\D*)(\d)ch(\d)$", t)
                if mm:
                    if mm.group(1) and AUDIO.get(mm.group(1)) and AUDIO[mm.group(1)] not in p.audio:
                        p.audio.append(AUDIO[mm.group(1)])
                    label = f"{mm.group(2)}.{mm.group(3)}"
            if label and label not in p.audio:
                p.audio.append(label)
        elif kind == "edition":
            p.edition = p.edition or EDITIONS[t]
        elif kind == "other":
            if t not in p.other:
                p.other.append(t)
        elif kind == "language":
            code = LANGUAGES[t]
            if code not in p.languages:
                p.languages.append(code)
        elif kind == "part":
            mm = _PART.match(t)
            if mm and p.part is None:
                p.part = int(mm.group(1))

    p.generic = bool(GENERIC_NAMES.match(p.title.replace(" ", "_"))) or not p.title
    if p.season is not None or p.episodes or p.absolute is not None or p.air_date:
        p.kind = "episode"
    elif p.title:
        p.kind = "movie"
    return p


# --------------------------------------------------------------------------- guessit merge

def _merge_guessit(p: Parsed, name: str) -> Parsed:
    try:
        g = dict(_guessit(name))
    except Exception:  # guessit must never break a scan
        return p
    p.engine = "guessit+builtin"
    gtitle = g.get("title")
    if isinstance(gtitle, str) and gtitle.strip() and not p.ids:
        # keep our title when we stripped a country code that guessit kept
        if not (p.country and gtitle.lower().endswith(p.country.lower())):
            p.title = gtitle.strip()
    if isinstance(g.get("year"), int) and p.year is None:
        p.year = g["year"]
    gtype = g.get("type")
    if gtype == "episode":
        p.kind = "episode"
        if isinstance(g.get("season"), int) and p.season is None:
            p.season = g["season"]
        ep = g.get("episode")
        if ep is not None and not p.episodes:
            p.episodes = list(ep) if isinstance(ep, list) else [ep]
        if isinstance(g.get("absolute_episode"), int) and p.absolute is None and p.season is None:
            p.absolute = g["absolute_episode"]
        if isinstance(g.get("episode_title"), str) and not p.episode_title:
            p.episode_title = g["episode_title"]
        if isinstance(g.get("date"), _dt.date) and not p.air_date:
            p.air_date = g["date"].isoformat()
    for key, attr in (("screen_size", "resolution"), ("source", "source"), ("video_codec", "video_codec"),
                      ("release_group", "release_group")):
        val = g.get(key)
        if isinstance(val, str) and not getattr(p, attr):
            setattr(p, attr, val)
    if isinstance(g.get("edition"), str) and not p.edition:
        p.edition = g["edition"]
    part = g.get("part") or g.get("cd")
    if isinstance(part, int) and p.part is None:
        p.part = part
    if p.season is not None or p.episodes or p.absolute is not None or p.air_date:
        p.kind = "episode"
    p.generic = bool(GENERIC_NAMES.match(p.title.replace(" ", "_"))) or not p.title
    return p


def parse_name(name: str, engine: str = "auto", is_file: bool = True) -> Parsed:
    """Parse one file or folder name. ``engine``: auto | builtin."""
    p = parse_builtin(name, is_file=is_file)
    if engine == "auto" and _guessit is not None and is_file:
        p = _merge_guessit(p, name)
    return p


# --------------------------------------------------------------------------- with folders

def parse_path(path: str | PurePath, engine: str = "auto") -> Parsed:
    """Parse a file using its name and the folders above it.

    ``TV Shows/The Office (US)/Season 3/03x01.mkv`` and
    ``Movies/Inception (2010)/movie.mkv`` both work.
    """
    path = PurePath(path)
    p = parse_name(path.name, engine)
    parents = [part for part in path.parts[:-1]][-3:]

    season_folder = None
    show_folder = None
    for i in range(len(parents) - 1, -1, -1):
        part = parents[i]
        m = _SEASON_ONLY.match(part.strip())
        if m or _SPECIALS_FOLDER.match(part.strip()):
            season_folder = 0 if not m else int(m.group(1) or m.group(2))
            if i > 0:
                show_folder = parents[i - 1]
            break

    if season_folder is not None:
        p.kind = "episode"
        if p.season is None:
            p.season = season_folder
        if show_folder and (p.generic or not p.title or p.title.isdigit() or not p.episodes):
            sp = parse_name(show_folder, engine, is_file=False)
            if sp.title:
                p.alt_titles.insert(0, (p.title, p.year)) if p.title and not p.generic else None
                p.title, p.year = sp.title, sp.year or p.year
                p.country = p.country or sp.country
                for k, v in sp.ids.items():
                    p.ids.setdefault(k, v)
        elif show_folder:
            sp = parse_name(show_folder, engine, is_file=False)
            for k, v in sp.ids.items():
                p.ids.setdefault(k, v)
            if sp.title and sp.title.lower() != p.title.lower():
                p.alt_titles.append((sp.title, sp.year))
        if not p.episodes and p.absolute is not None:
            p.episodes, p.absolute = [p.absolute], None
        if not p.episodes and p.absolute is None:
            m = re.search(r"(?:^|\D)(\d{1,4})(?:\D|$)", PurePath(path.name).stem)
            if m:
                num = m.group(1)
                season_txt = str(p.season)
                if len(num) >= 3 and num.startswith(season_txt) and len(num) - len(season_txt) == 2:
                    p.episodes = [int(num[len(season_txt):])]   # "203" in Season 2 = episode 3
                else:
                    p.episodes = [int(num)]
        p.generic = not p.title
        return p

    if parents:
        fp = parse_name(parents[-1], engine, is_file=False)
        for k, v in fp.ids.items():
            p.ids.setdefault(k, v)
        if p.kind != "episode":
            if (p.generic or not p.title) and fp.title and not fp.generic:
                p.title, p.year = fp.title, fp.year or p.year
                p.edition = p.edition or fp.edition
                p.kind, p.generic = "movie", False
            elif fp.title and not fp.generic and fp.year and (p.year is None or fp.year == p.year):
                if fp.title.lower() != p.title.lower():
                    p.alt_titles.append((fp.title, fp.year))
                if p.year is None:
                    p.year = fp.year
        elif p.title and fp.title and fp.title.lower() != p.title.lower() and not fp.is_episode:
            p.alt_titles.append((fp.title, fp.year))
    return p
