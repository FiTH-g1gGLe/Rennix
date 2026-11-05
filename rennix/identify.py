"""The identification engine: works out what each file is.

The rule above all others: a wrong green match is the worst possible outcome,
because it renames a file silently and wrongly. Amber is always better than a
confident mistake.

Stages (see the plan's "Identification engine" section):
 1. gather evidence      - name, folders, IDs, .nfo, runtime (parse.py, mediainfo.py)
 2. classify             - episode, film, extra
 3. several readings     - parse.Parsed.alt_titles
 4. group episodes       - one show decision per group
 5. search in tiers      - exact ID, title + year, title only, alternative titles, fuzzy
 6. validate candidates  - episode exists, episode title, runtime, year, country
 7. score and decide     - confidence AND a clear lead over second place
"""

from __future__ import annotations

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import mediainfo
from .diagnostics import friendly, log_event
from .parse import Parsed, parse_path
from .scanner import MediaFile
from .textnorm import best_similarity, normalize, similarity
from .tmdb import Source, TMDbError, poster_url

log = logging.getLogger("rennix.identify")

THRESHOLDS = {
    # green needs confidence >= first number and a lead over 2nd place >= second number
    "relaxed": (0.82, 0.10),
    "normal": (0.88, 0.15),
    "strict": (0.94, 0.22),
}
AMBER_MIN = 0.50
TITLE_WEIGHT = 0.6


# --------------------------------------------------------------------------- data

@dataclass
class Candidate:
    kind: str                 # movie | tv
    tmdb_id: int
    title: str
    original_title: str = ""
    year: int | None = None
    poster: str = ""
    popularity: float = 0.0
    overview: str = ""
    runtime: int | None = None
    alt_titles: list = field(default_factory=list)
    origin_country: list = field(default_factory=list)
    confidence: float = 0.0
    reasons: list = field(default_factory=list)
    hints: list = field(default_factory=list)      # short lines for the match panel

    def to_dict(self) -> dict:
        d = asdict(self)
        d["poster_url"] = poster_url(self.poster)
        d["confidence"] = round(self.confidence, 3)
        return d


@dataclass
class Match:
    kind: str                  # movie | episode
    tmdb_id: int               # movie id, or show id for episodes
    title: str                 # film title, or show name
    year: int | None = None
    original_title: str = ""
    imdb_id: str = ""
    poster: str = ""
    collection: str = ""
    runtime: int | None = None
    season: int | None = None
    episodes: list = field(default_factory=list)
    episode_titles: list = field(default_factory=list)
    air_date: str = ""
    absolute: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["poster_url"] = poster_url(self.poster)
        return d


@dataclass
class Identified:
    file: MediaFile
    parsed: Parsed
    probe: dict = field(default_factory=dict)
    status: str = "pending"    # pending | green | amber | red
    confidence: float = 0.0
    match: Match | None = None
    candidates: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    message: str = ""
    group: str = ""

    def to_dict(self) -> dict:
        p = self.parsed
        return {
            "id": self.file.id,
            "path": str(self.file.path),
            "name": self.file.name,
            "folder": str(self.file.path.parent),
            "size": self.file.size,
            "category": self.file.category,
            "companions": [str(c.path) for c in self.file.companions],
            "status": self.status,
            "confidence": round(self.confidence, 3),
            "match": self.match.to_dict() if self.match else None,
            "candidates": [c.to_dict() for c in self.candidates[:6]],
            "reasons": self.reasons,
            "message": self.message,
            "group": self.group,
            "parsed": {
                "kind": p.kind, "title": p.title, "year": p.year, "season": p.season,
                "episodes": p.episodes, "absolute": p.absolute, "air_date": p.air_date,
                "resolution": p.resolution or self.probe.get("resolution", ""),
                "minutes": self.probe.get("minutes"),
            },
        }


# --------------------------------------------------------------------------- helpers

def _year(date: str | None) -> int | None:
    if date and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def _fmt_minutes(m: float | None) -> str:
    if not m:
        return "?"
    m = int(round(m))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def _popularity_bonus(pop: float) -> float:
    return 0.01 * min(1.0, math.log10((pop or 0) + 1) / 3)


def _read_nfo_ids(file: MediaFile) -> dict:
    ids: dict = {}
    for c in file.companions:
        if c.path.suffix.lower() != ".nfo":
            continue
        try:
            text = c.path.read_text(encoding="utf-8", errors="ignore")[:200_000]
        except OSError:
            continue
        m = re.search(r"(tt\d{7,9})", text)
        if m:
            ids.setdefault("imdb", m.group(1))
        m = re.search(r"<tmdbid>(\d+)</tmdbid>|<uniqueid[^>]*type=\"tmdb\"[^>]*>(\d+)<", text, re.I)
        if m:
            ids.setdefault("tmdb", int(m.group(1) or m.group(2)))
        m = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", text)
        if m:
            ids.setdefault("tmdb", int(m.group(2)))
    return ids


def _runtime_score(file_min: float | None, tmdb_min: int | None, extended: bool) -> float | None:
    """1.0 = fits, 0.0 = clearly a different film. None = can't tell."""
    if not file_min or not tmdb_min:
        return None
    diff = abs(file_min - tmdb_min)
    if diff <= 4 or diff / tmdb_min <= 0.04:
        return 1.0
    if extended and file_min > tmdb_min and file_min <= tmdb_min * 1.3:
        return 0.8
    if file_min > tmdb_min and file_min <= tmdb_min * 1.25 and diff <= 40:
        return 0.55  # possibly an extended cut nobody labelled
    if diff <= 10:
        return 0.5
    return 0.0


def _year_score(file_year: int | None, cand_year: int | None) -> float | None:
    if not file_year or not cand_year:
        return None
    if file_year == cand_year:
        return 1.0
    if abs(file_year - cand_year) == 1:
        return 0.7
    return 0.0


def _decide(conf: float, margin: float, strong: bool, strictness: str) -> str:
    green, lead = THRESHOLDS.get(strictness, THRESHOLDS["normal"])
    if conf >= green and margin >= lead and strong:
        return "green"
    if conf >= AMBER_MIN:
        return "amber"
    return "red"


def _honest_confidence(conf: float, margin: float, strong: bool, strictness: str) -> float:
    """The number shown to the user: never high when Rennix isn't actually sure."""
    green, lead = THRESHOLDS.get(strictness, THRESHOLDS["normal"])
    if margin < lead:
        conf = min(conf, 0.55 + margin)
    if not strong:
        conf = min(conf, 0.79)
    return conf


def map_absolute(seasons: list[dict], absolute: int) -> tuple[int, int] | None:
    """Turn 'episode 147' into (season, episode) using the season lengths."""
    remaining = absolute
    for s in sorted((s for s in seasons if s.get("season_number", 0) > 0),
                    key=lambda s: s["season_number"]):
        count = s.get("episode_count") or 0
        if remaining <= count:
            return s["season_number"], remaining
        remaining -= count
    return None


# --------------------------------------------------------------------------- engine

class Identifier:
    def __init__(self, source: Source, learned=None, strictness: str = "normal",
                 probe_fn: Callable[[Path], dict] | None = mediainfo.probe,
                 max_workers: int = 6, mode: str = "auto"):
        self.src = source
        self.learned = learned
        self.strictness = strictness
        self.probe_fn = probe_fn
        self.max_workers = max(1, max_workers)
        self.mode = mode            # auto | movie | tv

    # ------------------------------------------------------------- entry point
    def identify(self, files: list[MediaFile],
                 progress: Callable[[Identified], None] | None = None) -> list[Identified]:
        items = self._prepare(files)
        done: dict[str, Identified] = {}

        def report(item: Identified):
            done[item.file.id] = item
            if progress:
                try:
                    progress(item)
                except Exception:
                    pass

        movies, groups, learned = [], {}, []
        for it in items:
            if self.learned is not None and self._learned_match(it):
                learned.append(it)
                continue
            if it.file.category == "extra" and not it.parsed.is_episode:
                it.status, it.message = "red", "Extra (trailer or featurette). Kept out of renaming."
                it.reasons = ["🎞️ Looks like an extra, not the main film"]
                report(it)
                continue
            if it.parsed.is_episode:
                p = it.parsed
                key = f"{normalize(p.title)}|{p.year or ''}|{p.country or ''}|{p.ids.get('tmdb', '')}"
                it.group = key
                groups.setdefault(key, []).append(it)
            else:
                movies.append(it)
        for it in learned:
            report(it)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(self._safe, self._identify_movie, it) for it in movies]
            futures += [pool.submit(self._safe_group, g) for g in groups.values()]
            for fut in as_completed(futures):
                for item in fut.result():
                    report(item)
        return [done[it.file.id] for it in items if it.file.id in done]

    def _safe(self, fn, item: Identified) -> list[Identified]:
        try:
            fn(item)
        except TMDbError as exc:
            item.status, item.message = "red", exc.friendly
            item.reasons = ["📡 " + exc.friendly]
            log_event(log, logging.WARNING, exc.code, "Lookup failed for a file", file=str(item.file.path))
        except Exception as exc:  # never let one file stop the batch
            item.status, item.message = "red", friendly("RNX-I001")
            log_event(log, logging.ERROR, "RNX-I001", f"Identifying a file failed: {exc}", exc,
                      file=str(item.file.path), parsed=item.parsed.title)
        self._log_result(item)
        return [item]

    def _safe_group(self, group: list[Identified]) -> list[Identified]:
        try:
            self._identify_group(group)
        except TMDbError as exc:
            for it in group:
                it.status, it.message = "red", exc.friendly
                it.reasons = ["📡 " + exc.friendly]
            log_event(log, logging.WARNING, exc.code, "Lookup failed for a show",
                      show=group[0].parsed.title, files=len(group))
        except Exception as exc:
            for it in group:
                it.status, it.message = "red", friendly("RNX-I001")
            log_event(log, logging.ERROR, "RNX-I001", f"Identifying a show failed: {exc}", exc,
                      show=group[0].parsed.title, files=[str(i.file.path) for i in group][:20])
        for it in group:
            self._log_result(it)
        return group

    @staticmethod
    def _log_result(it: Identified) -> None:
        m = it.match
        what = ""
        if m is not None:
            what = f"{m.title} ({m.year})" + (f" S{m.season:02d}E{m.episodes[0]:02d}" if m.kind == "episode" and m.season is not None and m.episodes else "")
        level = logging.INFO if it.status != "red" else logging.WARNING
        log.log(level, "%s → %s %s", it.file.name, it.status.upper(), what or it.message,
                extra={"ctx": {"confidence": round(it.confidence, 2), "reasons": it.reasons[:6],
                               "parsed": {"title": it.parsed.title, "year": it.parsed.year,
                                          "season": it.parsed.season, "episodes": it.parsed.episodes},
                               "candidates": [f"{c.title} ({c.year}) {c.confidence:.2f}" for c in it.candidates[:4]]}})

    # ------------------------------------------------------------- preparation
    def _prepare(self, files: list[MediaFile]) -> list[Identified]:
        def one(f: MediaFile) -> Identified:
            p = parse_path(f.path)
            for k, v in _read_nfo_ids(f).items():
                p.ids.setdefault(k, v)
            if self.mode == "movie" and p.kind != "movie":
                p.kind = "movie"
                p.season, p.episodes, p.absolute, p.air_date = None, [], None, None
            elif self.mode == "tv" and p.kind != "episode":
                p.kind = "episode"
            probe = {}
            if self.probe_fn is not None:
                try:
                    probe = self.probe_fn(f.path) or {}
                except Exception:
                    probe = {}
            return Identified(file=f, parsed=p, probe=probe)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            return list(pool.map(one, files))

    # ------------------------------------------------------------- learned
    def _learned_match(self, it: Identified) -> bool:
        rec = self.learned.lookup(it.file.name, it.file.size)
        if not rec:
            return False
        try:
            if rec["kind"] == "movie":
                details = self.src.movie(rec["tmdb_id"])
                it.match = self._movie_match(details)
            else:
                show = self.src.tv(rec["tmdb_id"])
                extra = rec.get("extra") or {}
                if extra.get("season") is not None:
                    it.parsed.season = extra["season"]
                    it.parsed.episodes = extra.get("episodes") or it.parsed.episodes
                it.match = self._episode_match(show, it.parsed)
                if it.match is None:
                    return False
        except TMDbError:
            return False
        it.status, it.confidence = "green", 1.0
        it.reasons = ["🧠 You picked this match before"]
        return True

    # ------------------------------------------------------------- movies
    def _candidate_from_movie(self, r: dict) -> Candidate:
        return Candidate(
            kind="movie", tmdb_id=r["id"], title=r.get("title") or r.get("name") or "",
            original_title=r.get("original_title") or "", year=_year(r.get("release_date")),
            poster=r.get("poster_path") or "", popularity=r.get("popularity") or 0.0,
            overview=r.get("overview") or "",
        )

    def _movie_match(self, d: dict) -> Match:
        coll = d.get("belongs_to_collection") or {}
        return Match(
            kind="movie", tmdb_id=d["id"], title=d.get("title") or "",
            year=_year(d.get("release_date")), original_title=d.get("original_title") or "",
            imdb_id=d.get("imdb_id") or (d.get("external_ids") or {}).get("imdb_id") or "",
            poster=d.get("poster_path") or "", collection=coll.get("name") or "",
            runtime=d.get("runtime") or None,
        )

    def _movie_hypotheses(self, p: Parsed) -> list[tuple[str, int | None]]:
        seen, out = set(), []
        for title, year in [(p.title, p.year)] + list(p.alt_titles):
            if not title or (p.generic and title == p.title):
                continue
            key = (normalize(title), year)
            if key in seen:
                continue
            seen.add(key)
            out.append((title, year))
        return out

    def _identify_movie(self, it: Identified) -> None:
        p = it.parsed
        minutes = it.probe.get("minutes")
        extended = bool(p.edition) or "extended" in p.raw.lower()

        # tier 1: exact ID
        if p.ids.get("tmdb"):
            d = self.src.movie(int(p.ids["tmdb"]))
            self._finish_by_id(it, d, "🔑 TheMovieDB ID in the name", minutes, extended)
            return
        if p.ids.get("imdb"):
            found = self.src.find_by_imdb(p.ids["imdb"])
            res = found.get("movie_results") or []
            if res:
                d = self.src.movie(res[0]["id"])
                self._finish_by_id(it, d, "🔑 IMDb ID in the name", minutes, extended)
                return

        hyps = self._movie_hypotheses(p)
        if not hyps:
            it.status = "red"
            it.message = friendly("RNX-I002")
            it.reasons = ["🔍 The name has no title to search for"]
            return

        # tiers 2-5: title + year, title only, alternative readings, shortened
        found: dict[int, Candidate] = {}
        for title, year in hyps:
            results = self.src.search_movie(title, year) if year else self.src.search_movie(title)
            if year and not results:
                results = self.src.search_movie(title)
            for r in results[:8]:
                found.setdefault(r["id"], self._candidate_from_movie(r))
            if any(similarity(title, c.title) >= 0.97 and (not year or c.year == year)
                   for c in found.values()):
                break
        if not found or max(best_similarity(hyps[0][0], [c.title, c.original_title])
                            for c in found.values()) < 0.6:
            words = hyps[0][0].split()
            if len(words) > 2:
                for r in self.src.search_movie(" ".join(words[:-1]), hyps[0][1])[:5]:
                    found.setdefault(r["id"], self._candidate_from_movie(r))

        if not found:
            it.status = "red"
            it.message = friendly("RNX-I003")
            it.reasons = ["🔍 Nothing on TheMovieDB matched this name"]
            return

        titles = [h[0] for h in hyps]
        years = {h[1] for h in hyps if h[1]}
        file_year = p.year

        def prelim(c: Candidate) -> float:
            s = best_similarity(c.title, titles + []) if titles else 0
            s = max(s, best_similarity(c.original_title, titles)) if c.original_title else s
            ys = _year_score(file_year, c.year) if file_year else (1.0 if c.year in years else None)
            return s + (0.3 * ys if ys is not None else 0)

        ranked = sorted(found.values(), key=prelim, reverse=True)[:6]
        # details for the top few: runtime and alternative titles
        for c in ranked[:3]:
            try:
                d = self.src.movie(c.tmdb_id)
            except TMDbError:
                continue
            c.runtime = d.get("runtime") or None
            c.alt_titles = [t.get("title") for t in (d.get("alternative_titles") or {}).get("titles", [])
                            if t.get("title")][:30]

        for c in ranked:
            names = [c.title, c.original_title] + c.alt_titles
            tsim = max(best_similarity(t, names) for t in titles)
            evidence, reasons, hints = [], [], []
            reasons.append("✓ Title matches" if tsim >= 0.95 else f"≈ Title is a {int(tsim * 100)}% match")
            ys = _year_score(file_year, c.year)
            if file_year and str(file_year) in c.title:
                ys = None   # "Wonder Woman 1984": the number is part of the title, not a year
            if ys is None and not file_year:
                # alternative reading with a year ("Wonder Woman 1984" read as title+year)
                for t, y in hyps:
                    if y and c.year == y and similarity(t, c.title) > 0.9:
                        ys = 1.0
            if ys is not None:
                evidence.append(ys)
                if ys >= 0.7:
                    reasons.append(f"✓ Year matches ({c.year})")
                else:
                    reasons.append(f"✗ Year differs ({file_year} vs {c.year})")
            rs = _runtime_score(minutes, c.runtime, extended)
            if rs is not None:
                evidence.append(rs)
                if rs >= 0.8:
                    reasons.append(f"✓ Runtime matches ({_fmt_minutes(c.runtime)})")
                    hints.append(f"Runtime matches your file ({_fmt_minutes(minutes)})")
                elif rs >= 0.5:
                    reasons.append(f"≈ Runtime is close ({_fmt_minutes(c.runtime)})")
                else:
                    diff = int(abs((minutes or 0) - (c.runtime or 0)))
                    reasons.append(f"✗ Runtime differs by {diff} min")
                    hints.append(f"Runtime is {diff} min {'shorter' if (c.runtime or 0) < (minutes or 0) else 'longer'}")
            conf = TITLE_WEIGHT * tsim + (1 - TITLE_WEIGHT) * (sum(evidence) / len(evidence) if evidence else 0)
            conf += _popularity_bonus(c.popularity)
            c.confidence = min(conf, 1.0)
            c.reasons, c.hints = reasons, hints
            c._strong = (ys is not None and ys >= 0.7)  # type: ignore[attr-defined]

        ranked.sort(key=lambda c: c.confidence, reverse=True)
        best = ranked[0]
        second = ranked[1].confidence if len(ranked) > 1 else 0.0
        margin = best.confidence - second
        it.candidates = ranked
        strong = getattr(best, "_strong", False)
        it.status = _decide(best.confidence, margin, strong, self.strictness)
        it.confidence = _honest_confidence(best.confidence, margin, strong, self.strictness)
        it.reasons = list(best.reasons)
        if it.status == "amber" and len(ranked) > 1 and margin < 0.15:
            it.reasons.append(f"⚠️ Close call with {ranked[1].title} ({ranked[1].year or '?'})")
            it.message = f"{len(ranked)} possible matches"
        elif it.status == "amber" and not getattr(best, "_strong", False):
            it.reasons.append("⚠️ No year in the name to confirm it")
            it.message = "Please check this match"
        elif it.status == "red":
            it.message = "No confident match. Pick one or search."
        if it.status in ("green", "amber"):
            try:
                it.match = self._movie_match(self.src.movie(best.tmdb_id))
            except TMDbError:
                it.match = Match(kind="movie", tmdb_id=best.tmdb_id, title=best.title,
                                 year=best.year, poster=best.poster, runtime=best.runtime)

    def _finish_by_id(self, it: Identified, d: dict, why: str, minutes, extended) -> None:
        it.match = self._movie_match(d)
        c = self._candidate_from_movie(d)
        c.runtime = it.match.runtime
        c.confidence = 1.0
        c.reasons = [why]
        it.candidates = [c]
        rs = _runtime_score(minutes, it.match.runtime, extended)
        if rs is not None and rs < 0.5:
            it.status, it.confidence = "amber", 0.7
            it.reasons = [why, f"⚠️ But the runtime differs ({_fmt_minutes(minutes)} vs {_fmt_minutes(it.match.runtime)})"]
            it.message = "ID found, but the runtime doesn't fit. Please check."
        else:
            it.status, it.confidence = "green", 1.0
            it.reasons = [why]

    # ------------------------------------------------------------- TV
    def _candidate_from_tv(self, r: dict) -> Candidate:
        return Candidate(
            kind="tv", tmdb_id=r["id"], title=r.get("name") or "",
            original_title=r.get("original_name") or "", year=_year(r.get("first_air_date")),
            poster=r.get("poster_path") or "", popularity=r.get("popularity") or 0.0,
            overview=r.get("overview") or "", origin_country=r.get("origin_country") or [],
        )

    def _resolve_episode(self, show: dict, p: Parsed) -> tuple[int | None, list[int]]:
        """Season and episode numbers for this file within this show (absolute/date aware)."""
        seasons = show.get("seasons") or []
        if p.season is not None and p.episodes:
            return p.season, list(p.episodes)
        if p.absolute is not None:
            if p.season is not None:
                return p.season, [p.absolute]
            mapped = map_absolute(seasons, p.absolute)
            if mapped:
                return mapped[0], [mapped[1]]
            return None, []
        if p.air_date:
            y = int(p.air_date[:4])
            for s in seasons:
                sy = _year(s.get("air_date"))
                if sy is None or abs(sy - y) > 1:
                    continue
                try:
                    data = self.src.season(show["id"], s["season_number"])
                except TMDbError:
                    continue
                for ep in data.get("episodes", []):
                    if ep.get("air_date") == p.air_date:
                        return s["season_number"], [ep["episode_number"]]
            return None, []
        return p.season, []

    def _episode_exists(self, show: dict, season: int | None, episodes: list[int]) -> bool:
        if season is None or not episodes:
            return False
        for s in show.get("seasons") or []:
            if s.get("season_number") == season:
                return max(episodes) <= (s.get("episode_count") or 0)
        return False

    def _season_data(self, show_id: int, season: int) -> dict:
        try:
            return self.src.season(show_id, season)
        except TMDbError:
            return {}

    def _episode_match(self, show: dict, p: Parsed) -> Match | None:
        season, eps = self._resolve_episode(show, p)
        if season is None or not eps:
            return None
        data = self._season_data(show["id"], season)
        by_num = {e.get("episode_number"): e for e in data.get("episodes", [])}
        titles = [(by_num.get(n) or {}).get("name") or "" for n in eps]
        first = by_num.get(eps[0]) or {}
        return Match(
            kind="episode", tmdb_id=show["id"], title=show.get("name") or "",
            year=_year(show.get("first_air_date")), original_title=show.get("original_name") or "",
            imdb_id=(show.get("external_ids") or {}).get("imdb_id") or "",
            poster=show.get("poster_path") or "", season=season, episodes=eps,
            episode_titles=titles, air_date=first.get("air_date") or "",
            runtime=first.get("runtime") or None, absolute=p.absolute,
        )

    def _show_hypotheses(self, group: list[Identified]) -> list[tuple[str, int | None]]:
        seen, out = set(), []
        p0 = group[0].parsed
        pairs = [(p0.title, p0.year)]
        for it in group:
            pairs += list(it.parsed.alt_titles)
        for t, y in pairs:
            if not t:
                continue
            key = (normalize(t), y)
            if key not in seen:
                seen.add(key)
                out.append((t, y))
        return out

    def _identify_group(self, group: list[Identified]) -> None:
        p0 = group[0].parsed
        shows: dict[int, dict] = {}
        forced: int | None = None

        if self.learned is not None:
            rule = self.learned.folder_rule(str(group[0].file.path.parent))
            if rule and rule["kind"] == "tv":
                forced = rule["tmdb_id"]
        if forced is None and p0.ids.get("tmdb"):
            forced = int(p0.ids["tmdb"])
        if forced is None and p0.ids.get("imdb"):
            res = self.src.find_by_imdb(p0.ids["imdb"])
            tvr = res.get("tv_results") or []
            epr = res.get("tv_episode_results") or []
            if tvr:
                forced = tvr[0]["id"]
            elif epr:
                forced = epr[0].get("show_id")

        candidates: dict[int, Candidate] = {}
        if forced:
            show = self.src.tv(forced)
            shows[forced] = show
            c = self._candidate_from_tv(show)
            c.confidence = 1.0
            candidates[forced] = c
        else:
            hyps = self._show_hypotheses(group)
            if not hyps:
                for it in group:
                    it.status, it.message = "red", friendly("RNX-I002")
                    it.reasons = ["🔍 The name has no show title to search for"]
                return
            for title, year in hyps:
                res = self.src.search_tv(title, year) if year else self.src.search_tv(title)
                if year and not res:
                    res = self.src.search_tv(title)
                for r in res[:8]:
                    candidates.setdefault(r["id"], self._candidate_from_tv(r))
            if not candidates:
                for it in group:
                    it.status, it.message = "red", friendly("RNX-I003")
                    it.reasons = ["🔍 Nothing on TheMovieDB matched this show name"]
                return

        titles = [h[0] for h in self._show_hypotheses(group)] or [p0.title]
        ranked = sorted(candidates.values(),
                        key=lambda c: best_similarity(c.title, titles) + 0.2 * (
                            (_year_score(p0.year, c.year) or 0) if p0.year else 0),
                        reverse=True)[:4]
        for c in ranked:
            if c.tmdb_id not in shows:
                try:
                    shows[c.tmdb_id] = self.src.tv(c.tmdb_id)
                except TMDbError:
                    continue
            d = shows[c.tmdb_id]
            c.origin_country = d.get("origin_country") or c.origin_country
            c.alt_titles = [t.get("title") for t in (d.get("alternative_titles") or {}).get("results", [])
                            if t.get("title")][:30]

        # score every candidate against every file in the group
        per_cand: dict[int, list[tuple[float, list[str], bool]]] = {}
        for c in ranked:
            show = shows.get(c.tmdb_id)
            if not show:
                continue
            names = [c.title, c.original_title] + c.alt_titles
            tsim = 1.0 if forced else max(best_similarity(t, names) for t in titles)
            scores = []
            season_cache: dict[int, dict] = {}
            for it in group:
                p = it.parsed
                season, eps = self._resolve_episode(show, p)
                exists = self._episode_exists(show, season, eps)
                evidence, reasons = [1.0 if exists else 0.0], []
                reasons.append("✓ Show title matches" if tsim >= 0.95 else f"≈ Show title is a {int(tsim * 100)}% match")
                label = f"S{season:02d}E{eps[0]:02d}" if season is not None and eps else "this episode"
                reasons.append(f"✓ {label} exists" if exists else f"✗ {label} doesn't exist in this show")
                ys = _year_score(p.year, c.year)
                if ys is not None:
                    evidence.append(ys)
                    reasons.append(f"✓ Year matches ({c.year})" if ys >= 0.7 else f"✗ Year differs ({p.year} vs {c.year})")
                if p.country:
                    ok = p.country in (c.origin_country or [])
                    evidence.append(1.0 if ok else 0.0)
                    reasons.append(f"✓ Country matches ({p.country})" if ok else f"✗ Not the {p.country} version")
                if exists and (p.episode_title or it.probe.get("minutes")) and season is not None:
                    if season not in season_cache:
                        season_cache[season] = self._season_data(show["id"], season)
                    epdata = {e.get("episode_number"): e for e in season_cache[season].get("episodes", [])}
                    ep = epdata.get(eps[0]) or {}
                    if p.episode_title and ep.get("name"):
                        es = similarity(p.episode_title, ep["name"])
                        evidence.append(1.0 if es >= 0.8 else 0.0)
                        reasons.append(f"✓ Episode title matches ({ep['name']})" if es >= 0.8
                                       else f"✗ Episode title differs ({ep['name']})")
                    rs = _runtime_score(it.probe.get("minutes"), ep.get("runtime"), False)
                    if rs is not None:
                        evidence.append(rs)
                        if rs >= 0.8:
                            reasons.append("✓ Runtime matches")
                conf = TITLE_WEIGHT * tsim + (1 - TITLE_WEIGHT) * (sum(evidence) / len(evidence))
                if not exists:
                    conf = min(conf, 0.3)
                conf = min(1.0, conf + _popularity_bonus(c.popularity))
                scores.append((conf, reasons, exists))
            per_cand[c.tmdb_id] = scores
            c._tsim = tsim  # type: ignore[attr-defined]
            c.confidence = sum(s[0] for s in scores) / len(scores) if scores else 0.0
            c.reasons = scores[0][1] if scores else []

        ranked = [c for c in ranked if c.tmdb_id in per_cand]
        ranked.sort(key=lambda c: c.confidence, reverse=True)
        if not ranked:
            for it in group:
                it.status, it.message = "red", "Couldn't load show details from TheMovieDB."
            return
        best = ranked[0]
        second = ranked[1].confidence if len(ranked) > 1 else 0.0
        margin = 1.0 if forced else best.confidence - second
        show = shows[best.tmdb_id]
        for idx, it in enumerate(group):
            conf, reasons, exists = per_cand[best.tmdb_id][idx]
            it.candidates = ranked
            it.confidence = conf
            if forced:
                reasons = ["🔑 Show ID found in the name or folder"] + reasons[1:]
            it.reasons = list(reasons)
            strong = exists and (bool(forced) or getattr(best, "_tsim", 0.0) >= 0.9)
            it.status = _decide(conf, margin, strong, self.strictness)
            it.confidence = _honest_confidence(conf, margin, strong, self.strictness)
            if it.status != "red":
                it.match = self._episode_match(show, it.parsed)
                if it.match is None:
                    it.status = "red"
            if it.status == "amber" and margin < 0.15 and len(ranked) > 1:
                it.reasons.append(f"⚠️ Close call with {ranked[1].title} ({ranked[1].year or '?'})")
                it.message = f"{len(ranked)} possible shows"
            elif it.status == "amber":
                it.message = "Please check this match"
            elif it.status == "red":
                it.message = (friendly("RNX-I004")
                              if not exists else "No confident match. Pick one or search.")

    # ------------------------------------------------------------- manual choice
    def search(self, kind: str, query: str, year: int | None = None) -> list[Candidate]:
        if kind == "tv":
            return [self._candidate_from_tv(r) for r in self.src.search_tv(query, year)[:12]]
        return [self._candidate_from_movie(r) for r in self.src.search_movie(query, year)[:12]]

    def apply_choice(self, it: Identified, kind: str, tmdb_id: int,
                     season: int | None = None, episodes: list[int] | None = None,
                     remember: bool = True) -> Identified:
        """The user picked a match by hand. Rennix remembers it for next time."""
        if kind == "movie":
            it.match = self._movie_match(self.src.movie(tmdb_id))
            it.parsed.kind = "movie"
        else:
            if season is not None:
                it.parsed.season = season
            if episodes:
                it.parsed.episodes = episodes
            show = self.src.tv(tmdb_id)
            it.match = self._episode_match(show, it.parsed)
            if it.match is None:
                it.status, it.message = "red", "Set the season and episode number for this file."
                return it
        it.status, it.confidence = "green", 1.0
        it.reasons = ["👆 You chose this match"]
        it.message = ""
        if remember and self.learned is not None:
            extra = {}
            if kind != "movie" and it.match:
                extra = {"season": it.match.season, "episodes": it.match.episodes}
            self.learned.remember(it.file.name, it.file.size, "movie" if kind == "movie" else "tv",
                                  tmdb_id, extra)
        return it
