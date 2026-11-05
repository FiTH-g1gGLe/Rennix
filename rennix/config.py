"""Settings, data folders and the TheMovieDB token.

Folders follow each system's conventions (via platformdirs):
  settings, custom formats, learned corrections -> config dir (e.g. %APPDATA%\\Rennix)
  undo history, rename journal, logs            -> data dir   (e.g. %LOCALAPPDATA%\\Rennix)
  match cache, poster thumbnails                -> cache dir  (e.g. %LOCALAPPDATA%\\Rennix\\Cache)

Portable mode: if a file named ``portable.txt`` sits next to the app, everything
is kept in a ``data`` folder beside it instead.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from . import APP_NAME

try:  # platformdirs is a runtime requirement, but keep imports safe for tests
    import platformdirs
except ImportError:  # pragma: no cover
    platformdirs = None


def app_dir() -> Path:
    """Folder the app runs from (the .exe folder when packaged)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Path to a bundled resource (works inside a PyInstaller build)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    if getattr(sys, "_MEIPASS", None):
        base = base / "rennix"
    return base.joinpath(*parts)


def is_portable() -> bool:
    return (app_dir() / "portable.txt").exists()


class Paths:
    """Where Rennix keeps its files. Created on first use."""

    def __init__(self, root: Path | None = None):
        if root is not None:
            self.config = root / "config"
            self.data = root / "data"
            self.cache = root / "cache"
        elif is_portable():
            base = app_dir() / "data"
            self.config = base / "config"
            self.data = base
            self.cache = base / "cache"
        elif platformdirs is not None:
            self.config = Path(platformdirs.user_config_dir(APP_NAME, appauthor=False, roaming=True))
            self.data = Path(platformdirs.user_data_dir(APP_NAME, appauthor=False, roaming=False))
            self.cache = Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False))
        else:  # pragma: no cover
            base = Path.home() / ".rennix"
            self.config, self.data, self.cache = base, base, base / "cache"
        for p in (self.config, self.data, self.cache, self.history, self.logs, self.posters):
            p.mkdir(parents=True, exist_ok=True)

    @property
    def settings_file(self) -> Path:
        return self.config / "settings.json"

    @property
    def formats_file(self) -> Path:
        return self.config / "formats.json"

    @property
    def store_db(self) -> Path:
        return self.config / "learned.db"

    @property
    def cache_db(self) -> Path:
        return self.cache / "cache.db"

    @property
    def journal_file(self) -> Path:
        return self.data / "journal.json"

    @property
    def history(self) -> Path:
        return self.data / "history"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    @property
    def posters(self) -> Path:
        return self.cache / "posters"


DEFAULT_SETTINGS: dict[str, Any] = {
    "general": {
        "theme": "dark",            # dark | light | system
        "default_mode": "auto",     # auto | movie | tv
        "text_size": 100,
        "reduce_motion": False,
        "first_run_done": False,
        "media_server": "plex",     # plex | jellyfin | emby | kodi | none
    },
    "library": {
        "movies_dir": "",
        "tv_dir": "",
        "sort_into_library": False,  # False = rename where files are
        "action": "move",            # move | copy | hardlink | symlink
        "tidy_empty_folders": True,
        "extras_folders": False,
    },
    "naming": {
        "movie_format": "Plex",      # name of a template or custom format
        "episode_format": "Plex",
        "rename_subtitles": True,
        "rename_images": True,
        "rename_nfo": True,
    },
    "matching": {
        "strictness": "normal",      # relaxed | normal | strict
        "language": "en-US",
        "region": "GB",
        "episode_order": "aired",    # aired | dvd (per show later)
        "fingerprint": False,
        "parallel_lookups": 6,
    },
    "conflicts": {"policy": "skip"},  # skip | number | replace_if_better | ask
    "leftovers": {"junk": "leave"},   # leave | folder | recycle
    "safety": {
        "confirm_before_rename": True,
        "skip_amber": True,
        "history_days": 365,         # 0 = forever
    },
    "notifications": {"on_finish": True},
    "integrations": {"explorer_menu": False},
    "network": {"proxy": "", "offline": False},
    "privacy": {"crash_reports": False},
    "updates": {"auto_check": True, "channel": "stable"},
    "tmdb": {"token": ""},
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON safely: temp file, flush to disk, then swap in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class Settings:
    """User settings, merged over defaults so new options appear automatically."""

    def __init__(self, paths: Paths):
        self.paths = paths
        self._lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULT_SETTINGS)
        if paths.settings_file.exists():
            try:
                saved = json.loads(paths.settings_file.read_text(encoding="utf-8"))
                self.data = _deep_merge(DEFAULT_SETTINGS, saved)
            except (OSError, ValueError):
                # A damaged settings file never stops the app; keep a copy for support.
                try:
                    paths.settings_file.replace(paths.settings_file.with_suffix(".broken.json"))
                except OSError:
                    pass

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.data.get(section, {}).get(key, default)

    def update(self, changes: dict) -> None:
        with self._lock:
            self.data = _deep_merge(self.data, changes)
            atomic_write_json(self.paths.settings_file, self.data)

    def reset_section(self, section: str) -> None:
        with self._lock:
            if section in DEFAULT_SETTINGS:
                self.data[section] = copy.deepcopy(DEFAULT_SETTINGS[section])
                atomic_write_json(self.paths.settings_file, self.data)

    def save(self) -> None:
        with self._lock:
            atomic_write_json(self.paths.settings_file, self.data)


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def tmdb_token(settings: Settings | None = None) -> str:
    """Find the TheMovieDB read token.

    Order: the user's own token in settings, the TMDB_READ_TOKEN environment
    variable, a private ``.env`` file next to the app, a token bundled by the
    build (``rennix/_bundled.py``), then Rennix's own built-in key.
    """
    if settings is not None:
        tok = (settings.get("tmdb", "token") or "").strip()
        if tok:
            return tok
    tok = os.environ.get("TMDB_READ_TOKEN", "").strip()
    if tok:
        return tok
    for candidate in (app_dir() / ".env", Path.cwd() / ".env"):
        tok = read_env_file(candidate).get("TMDB_READ_TOKEN", "")
        if tok:
            return tok
    try:
        from ._bundled import TMDB_READ_TOKEN  # type: ignore

        if TMDB_READ_TOKEN:
            return TMDB_READ_TOKEN
    except ImportError:
        pass
    return _builtin_token()


# Rennix's own read-only TheMovieDB key, so the app works out of the box.
# Lightly encoded only to keep it away from automated scrapers.
_BUILTIN = "bzd2X1prLWdiWnJzT2VYajl0eXZqdDR0TkhKT3EyYzJ4MlJicFFzYms5VC5RZnhvakl1OVdhekpYWjJKQ0xkSkNaaFZtY2ZsR2NoSnlXNkl5Y2xCM2JqTm5Jc0lpWTRFRE1rVldNNGNUTmlSV1p5VUdPa0pXT21OV1poWmpJNklpWTFObklza1RNMjRTTnlNVE54a3pNNWNUTTZJaVppNW1Jc0lTTzFnallqaHpNd01UTnpZbVpsUkdPeE1HTTVjelltSldNMFlET2haV01oSmlPaVFXZGhKeWUuOUppTjFJelVJSmlPaWNHYmhKeWU="


def _builtin_token() -> str:
    import base64

    try:
        return base64.b64decode(_BUILTIN).decode()[::-1]
    except Exception:
        return ""
