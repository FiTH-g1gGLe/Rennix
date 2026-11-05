"""TheMovieDB client: searches, details, episode lists and posters.

* Retries dropped connections and "slow down" (429) replies with a short wait.
* Caches every answer locally, so repeat lookups are instant and work offline.
* Limits how many requests run at once.

This product uses the TMDB API but is not endorsed or certified by TMDB.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Protocol

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

API = "https://api.themoviedb.org/3"
IMAGE = "https://image.tmdb.org/t/p/"


class TMDbError(Exception):
    """A lookup failed; ``friendly`` is safe to show in the app."""

    def __init__(self, message: str, friendly: str = ""):
        super().__init__(message)
        self.friendly = friendly or "Couldn't reach TheMovieDB. Check your internet connection."


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
            raise TMDbError("offline", "Offline mode is on and this hasn't been looked up before.")
        if self.session is None:
            raise TMDbError("requests missing", "Rennix is missing a component (requests).")
        if not self.token:
            raise TMDbError("no token", "No TheMovieDB key is set. Add one in Settings → Matching.")

        query = dict(params)
        if re.fullmatch(r"[0-9a-f]{32}", self.token):
            query["api_key"] = self.token

        last_error = ""
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
                    time.sleep(1.5 * (2 ** attempt))
                    continue
            if resp.status_code == 200:
                data = resp.json()
                if self.cache is not None:
                    self.cache.set(key, data)
                return data
            if resp.status_code == 429:
                retry = float(resp.headers.get("Retry-After", "2") or 2)
                with self._pause_lock:
                    self._pause_until = max(self._pause_until, time.time() + retry)
                last_error = "rate limited"
                continue
            if resp.status_code == 401:
                raise TMDbError("401", "TheMovieDB didn't accept the key. Check it in Settings → Matching.")
            if resp.status_code == 404:
                raise TMDbError("404", "TheMovieDB has no entry for that.")
            if resp.status_code >= 500:
                last_error = f"server {resp.status_code}"
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise TMDbError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        if self.cache is not None:
            stale = self.cache.get(key, allow_stale=True)
            if stale is not None:
                return stale
        raise TMDbError(last_error or "failed")

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
