"""TheMovieDB client: searches, details, episode lists and posters.

* Retries dropped connections and "slow down" (429) replies with a short wait.
* Caches every answer locally, so repeat lookups are instant and work offline.
* Limits how many requests run at once.

This product uses the TMDB API but is not endorsed or certified by TMDB.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Protocol

from .diagnostics import friendly as _friendly, log_event

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

API = "https://api.themoviedb.org/3"
IMAGE = "https://image.tmdb.org/t/p/"


log = logging.getLogger("rennix.tmdb")


class TMDbError(Exception):
    """A lookup failed; ``friendly`` is safe to show in the app and ends with its code."""

    def __init__(self, message: str, friendly: str = "", code: str = "RNX-T001"):
        super().__init__(message)
        self.code = code
        self.friendly = _friendly(code, friendly)


class Source(Protocol):
    """What the identification engine needs. Tests use a fake with the same shape."""

    def search_movie(self, query: str, year: int | None = None) -> list[dict]: ...
    def search_tv(self, query: str, year: int | None = None) -> list[dict]: ...
    def find_by_imdb(self, imdb_id: str) -> dict: ...
    def movie(self, movie_id: int) -> dict: ...
    def tv(self, tv_id: int) -> dict: ...
    def season(self, tv_id: int, season_number: int) -> dict: ...


def poster_url(path: str | None, size: str = "w185") -> str:
    return f"{IMAGE}{size}{path}" if path else ""


class TMDbClient:
    def __init__(self, token: str, cache=None, language: str = "en-US", region: str = "",
                 max_parallel: int = 6, offline: bool = False, proxy: str = "",
                 timeout: float = 15.0):
        self.token = token.strip()
        self.cache = cache
        self.language = language
        self.region = region
        self.offline = offline
        self.timeout = timeout
        self._sem = threading.Semaphore(max(1, max_parallel))
        self._pause_until = 0.0
        self._pause_lock = threading.Lock()
        self.session = requests.Session() if requests else None
        if self.session is not None:
            self.session.headers["Accept"] = "application/json"
            self.session.headers["User-Agent"] = "Rennix/0.1 (+https://github.com/FiTH-g1gGLe/Rennix)"
            if proxy:
                self.session.proxies = {"http": proxy, "https": proxy}
            if self.token and not re.fullmatch(r"[0-9a-f]{32}", self.token):
                self.session.headers["Authorization"] = f"Bearer {self.token}"

    # ------------------------------------------------------------------ core
    def _key(self, path: str, params: dict) -> str:
        items = "&".join(f"{k}={params[k]}" for k in sorted(params))
        return f"{path}?{items}"

    def _get(self, path: str, params: dict | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        params.setdefault("language", self.language)
        key = self._key(path, params)
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return hit
        if self.offline:
            stale = self.cache.get(key, allow_stale=True) if self.cache is not None else None
            if stale is not None:
                return stale
            raise TMDbError("offline", code="RNX-T006")
        if self.session is None:
            raise TMDbError("requests missing", "Rennix is missing a component (requests).", code="RNX-X001")
        if not self.token:
            raise TMDbError("no token", code="RNX-T007")

        query = dict(params)
        if re.fullmatch(r"[0-9a-f]{32}", self.token):
            query["api_key"] = self.token

        last_error = ""
        last_code = "RNX-T001"
        started = time.time()
        for attempt in range(4):
            with self._pause_lock:
                wait = self._pause_until - time.time()
            if wait > 0:
                time.sleep(min(wait, 10))
            with self._sem:
                try:
                    resp = self.session.get(API + path, params=query, timeout=self.timeout)
                except requests.RequestException as exc:  # connection dropped, DNS, timeout
                    last_error = str(exc)
                    last_code = "RNX-T001"
                    log_event(log, logging.WARNING, "RNX-T001", f"Connection problem (try {attempt + 1} of 4)",
                              path=path, error=str(exc)[:300])
                    time.sleep(1.5 * (2 ** attempt))
                    continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError as exc:
                    log_event(log, logging.ERROR, "RNX-T008", path=path, body=resp.text[:300])
                    raise TMDbError("bad json", code="RNX-T008") from exc
                log.debug("GET %s %s (%.0f ms)", path, {k: v for k, v in params.items() if k != "language"},
                          (time.time() - started) * 1000)
                if self.cache is not None:
                    self.cache.set(key, data)
                return data
            if resp.status_code == 429:
                retry = float(resp.headers.get("Retry-After", "2") or 2)
                with self._pause_lock:
                    self._pause_until = max(self._pause_until, time.time() + retry)
                log_event(log, logging.INFO, "RNX-T004", f"Asked to slow down; waiting {retry:.0f}s", path=path)
                last_error, last_code = "rate limited", "RNX-T004"
                continue
            if resp.status_code == 401:
                log_event(log, logging.ERROR, "RNX-T002", path=path, status=401)
                raise TMDbError("401", code="RNX-T002")
            if resp.status_code == 404:
                log_event(log, logging.INFO, "RNX-T003", path=path, status=404)
                raise TMDbError("404", code="RNX-T003")
            if resp.status_code >= 500:
                last_error, last_code = f"server {resp.status_code}", "RNX-T005"
                log_event(log, logging.WARNING, "RNX-T005", f"Server error {resp.status_code} (try {attempt + 1} of 4)",
                          path=path)
                time.sleep(1.5 * (2 ** attempt))
                continue
            log_event(log, logging.ERROR, "RNX-T008", f"Unexpected reply {resp.status_code}", path=path,
                      body=resp.text[:300])
            raise TMDbError(f"HTTP {resp.status_code}: {resp.text[:200]}", code="RNX-T008")
        if self.cache is not None:
            stale = self.cache.get(key, allow_stale=True)
            if stale is not None:
                log.warning("Using an older saved answer for %s because TheMovieDB couldn't be reached", path)
                return stale
        log_event(log, logging.ERROR, last_code, "Gave up after 4 tries", path=path, error=last_error[:300])
        raise TMDbError(last_error or "failed", code=last_code)

    # ------------------------------------------------------------------ API
    def search_movie(self, query: str, year: int | None = None) -> list[dict]:
        data = self._get("/search/movie", {"query": query, "year": year, "include_adult": "false",
                                           "region": self.region})
        return data.get("results", [])

    def search_tv(self, query: str, year: int | None = None) -> list[dict]:
        data = self._get("/search/tv", {"query": query, "first_air_date_year": year,
                                        "include_adult": "false"})
        return data.get("results", [])

    def find_by_imdb(self, imdb_id: str) -> dict:
        return self._get(f"/find/{imdb_id}", {"external_source": "imdb_id"})

    def movie(self, movie_id: int) -> dict:
        return self._get(f"/movie/{movie_id}",
                         {"append_to_response": "alternative_titles,external_ids"})

    def tv(self, tv_id: int) -> dict:
        return self._get(f"/tv/{tv_id}",
                         {"append_to_response": "alternative_titles,external_ids,episode_groups"})

    def season(self, tv_id: int, season_number: int) -> dict:
        return self._get(f"/tv/{tv_id}/season/{season_number}")

    def episode_group(self, group_id: str) -> dict:
        return self._get(f"/tv/episode_group/{group_id}")

    def check(self) -> tuple[bool, str]:
        """Quick 'can I reach TMDb with this key?' test for first launch."""
        try:
            self._get("/configuration", {"language": ""})
            return True, "Connected to TheMovieDB."
        except TMDbError as exc:
            return False, exc.friendly
