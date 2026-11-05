"""The bridge between the app window (HTML/JS) and the Python engine.

Every public method here can be called from JavaScript as
``await pywebview.api.method_name(...)``. Long jobs (scanning, identifying,
renaming) run in a background thread and report back with events, so the
window never freezes.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path
from typing import Any

import functools
import platform

from . import __version__, mediainfo
from .diagnostics import BUFFER, CODES, friendly, log_event, report, setup_logging
from .config import Paths, Settings, tmdb_token
from .identify import Identified, Identifier
from .naming import (PRESETS, SAMPLE_FACTS, TOKENS, FormatError, Formats, facts_for,
                     format_to_text, parse_format, parts_from_json, parts_to_json, render,
                     validate)
from .renamer import Renamer, build_plan
from .scanner import VIDEO_EXTS, scan
from .store import Cache, Learned
from .tmdb import TMDbClient, TMDbError

log = logging.getLogger("rennix.app")
GITHUB_REPO = "FiTH-g1gGLe/Rennix"


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit not in ("B", "KB") else f"{int(size)} {unit}"
        size /= 1024
    return f"{n} B"


class Api:
    """Only methods are public: pywebview exposes every public attribute to the page,
    so all state is kept in underscore names."""

    def __init__(self, paths: Paths | None = None, source=None):
        self._paths = paths or Paths()
        setup_logging(self._paths.logs)
        self._settings = Settings(self._paths)
        self._formats = Formats(self._paths.formats_file)
        self._cache = Cache(self._paths.cache_db)
        self._learned = Learned(self._paths.store_db)
        self._window = None
        self._items: dict[str, Identified] = {}
        self._order: list[str] = []
        self._junk: list[str] = []
        self._source = source          # tests inject a fake TheMovieDB here
        self._busy = threading.Lock()
        self._id_offset = 0
        log.info("Rennix %s starting on %s (Python %s, %s)", __version__, platform.platform(),
                 platform.python_version(), "packaged" if getattr(sys, "frozen", False) else "from source",
                 extra={"ctx": {"mediainfo": mediainfo.available(), "data": str(self._paths.data),
                                "key": "custom" if self._settings.get("tmdb", "token") else "built-in"}})

    # ------------------------------------------------------------------ plumbing
    def _emit(self, event: str, payload: Any) -> None:
        if self._window is None:
            return
        try:
            self._window.evaluate_js(
                f"window.rennix && window.rennix.on({json.dumps(event)}, {json.dumps(payload, default=str)})"
            )
        except Exception:
            log.exception("emit failed")

    def _client(self):
        if self._source is not None:
            return self._source
        s = self._settings
        return TMDbClient(
            tmdb_token(s), cache=self._cache, language=s.get("matching", "language", "en-US"),
            region=s.get("matching", "region", ""), max_parallel=int(s.get("matching", "parallel_lookups", 6)),
            offline=bool(s.get("network", "offline", False)), proxy=s.get("network", "proxy", ""),
        )

    def _identifier(self, mode: str = "auto") -> Identifier:
        s = self._settings
        return Identifier(self._client(), learned=self._learned,
                          strictness=s.get("matching", "strictness", "normal"),
                          max_workers=int(s.get("matching", "parallel_lookups", 6)), mode=mode)

    def _item_dict(self, it: Identified, preview: dict | None = None) -> dict:
        d = it.to_dict()
        d["size_text"] = _human_size(it.file.size)
        if preview is not None:
            d["preview"] = preview.get(it.file.id)
        return d

    def _previews(self, ids: set | None = None) -> dict:
        items = [self._items[i] for i in self._order if i in self._items and (ids is None or i in ids)]
        plan = build_plan(items, self._formats, self._settings.data, include_amber=True)
        out: dict = {}
        for op in plan.ops:
            if op.kind == "video":
                dst = Path(op.dst)
                out[op.file_id] = {"name": dst.name, "folder": str(dst.parent), "full": str(dst)}
        for p in plan.problems:
            out.setdefault(p["file_id"], {})["problem"] = p["message"]
        for sk in plan.skipped:
            if sk["reason"] == "Already named correctly":
                out.setdefault(sk["file_id"], {})["same"] = True
        return out

    # ------------------------------------------------------------------ state
    def get_state(self) -> dict:
        return {
            "version": __version__,
            "settings": self._settings.data,
            "formats": self.list_formats(),
            "tokens": TOKENS,
            "token_set": bool(tmdb_token(self._settings)),
            "mediainfo": mediainfo.available(),
            "pending_journal": self.pending_journal(),
            "stats": self._learned.stats(),
            "platform": sys.platform,
            "items": [self._item_dict(self._items[i]) for i in self._order if i in self._items],
        }

    def save_settings(self, changes: dict) -> dict:
        self._settings.update(changes or {})
        return self._settings.data

    def reset_settings(self, section: str) -> dict:
        self._settings.reset_section(section)
        return self._settings.data

    def check_tmdb(self) -> dict:
        client = self._client()
        if hasattr(client, "check"):
            ok, msg = client.check()
        else:
            ok, msg = True, "Connected to TheMovieDB."
        return {"ok": ok, "message": msg}

    # ------------------------------------------------------------------ dialogs
    def choose_folder(self) -> list[str]:
        if self._window is None:
            return []
        import webview
        res = self._window.create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=False)
        return list(res or [])

    def choose_files(self) -> list[str]:
        if self._window is None:
            return []
        import webview
        exts = ";".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        res = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=(f"Video files ({exts})", "All files (*.*)"),
        )
        return list(res or [])

    def open_folder(self, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            p = p.parent
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
            return True
        except OSError:
            return False

    def open_url(self, url: str) -> bool:
        if not url.startswith(("https://www.themoviedb.org/", "https://github.com/", "https://www.imdb.com/")):
            return False
        webbrowser.open(url)
        return True

    def open_logs(self) -> bool:
        return self.open_folder(str(self._paths.logs))

    # ------------------------------------------------------------------ scanning and matching
    def add_paths(self, paths: list[str], mode: str = "auto") -> dict:
        paths = [p for p in (paths or []) if p]
        if not paths:
            return {"started": False, "message": "Nothing to add."}
        if not self._busy.acquire(blocking=False):
            return {"started": False, "message": "Rennix is still working on the last batch."}
        threading.Thread(target=self._scan_and_identify, args=(paths, mode), daemon=True).start()
        return {"started": True}

    def _scan_and_identify(self, paths: list[str], mode: str) -> None:
        try:
            self._emit("status", {"text": "Looking for videos…"})
            res = scan(paths)
            known = {str(self._items[i].file.path) for i in self._order if i in self._items}
            files = []
            for f in res.files:
                if str(f.path) in known:
                    continue
                self._id_offset += 1
                f.id = f"f{self._id_offset}"
                files.append(f)
            self._junk = sorted({*self._junk, *(str(j) for j in res.junk)})
            rows = [{"id": f.id, "name": f.name, "path": str(f.path), "folder": str(f.path.parent),
                     "size_text": _human_size(f.size), "status": "pending", "category": f.category}
                    for f in files]
            for p, m in res.errors:
                code = "RNX-S001" if "not found" in str(m).lower() or "no such" in str(m).lower() else "RNX-S002"
                log_event(log, logging.WARNING, code, f"Couldn't read: {m}", path=str(p))
            log.info("Scan found %d new video(s), skipped %d sample(s), %d junk file(s)", len(files),
                     len(res.skipped), len(res.junk), extra={"ctx": {"paths": paths}})
            self._emit("scanned", {"files": rows, "skipped": len(res.skipped),
                                   "errors": [{"path": str(p), "message": m} for p, m in res.errors],
                                   "junk": len(res.junk)})
            if not files:
                self._emit("done", {"count": 0})
                return
            ident = self._identifier(mode)

            def progress(it: Identified):
                self._items[it.file.id] = it
                if it.file.id not in self._order:
                    self._order.append(it.file.id)
                pv = self._previews({it.file.id}) if it.match else {}
                self._emit("result", self._item_dict(it, pv))

            ident.identify(files, progress)
            self._emit("done", {"count": len(files), "previews": self._previews()})
        except Exception as exc:
            log_event(log, logging.ERROR, "RNX-X001", f"Scanning stopped: {exc}", exc, paths=paths)
            self._emit("error", {"message": friendly("RNX-X001", "Something went wrong while scanning.")})
        finally:
            self._busy.release()

    def refresh_previews(self) -> dict:
        return self._previews()

    def clear(self, ids: list[str] | None = None) -> dict:
        if ids:
            for i in ids:
                self._items.pop(i, None)
            self._order = [i for i in self._order if i in self._items]
        else:
            self._items.clear()
            self._order.clear()
            self._junk.clear()
        return {"count": len(self._order)}

    def search(self, kind: str, query: str, year: Any = None) -> dict:
        try:
            y = int(year) if year not in (None, "", "null") else None
        except (TypeError, ValueError):
            y = None
        try:
            cands = self._identifier().search("tv" if kind == "tv" else "movie", query, y)
            return {"results": [c.to_dict() for c in cands]}
        except TMDbError as exc:
            return {"results": [], "message": exc.friendly}

    def choose_match(self, file_id: str, kind: str, tmdb_id: int, season: Any = None,
                     episodes: Any = None) -> dict:
        it = self._items.get(file_id)
        if it is None:
            return {"error": "That file isn't in the list any more."}
        try:
            s = int(season) if season not in (None, "") else None
            eps = [int(e) for e in str(episodes).replace(" ", "").split("-") if e] if episodes not in (None, "") else None
            if eps and len(eps) == 2 and eps[1] > eps[0]:
                eps = list(range(eps[0], eps[1] + 1))
        except ValueError:
            return {"error": "Season and episode must be numbers."}
        try:
            self._identifier().apply_choice(it, "movie" if kind == "movie" else "tv", int(tmdb_id), s, eps)
        except TMDbError as exc:
            return {"error": exc.friendly}
        return self._item_dict(it, self._previews({file_id}))

    def remember_folder(self, file_id: str) -> dict:
        """'Everything in this folder is this show' rule."""
        it = self._items.get(file_id)
        if it is None or it.match is None or it.match.kind != "episode":
            return {"ok": False}
        self._learned.set_folder_rule(str(it.file.path.parent), "tv", it.match.tmdb_id)
        return {"ok": True}

    # ------------------------------------------------------------------ renaming
    def plan(self, include_amber: bool = False, ids: list[str] | None = None) -> dict:
        items = [self._items[i] for i in self._order if i in self._items]
        p = build_plan(items, self._formats, self._settings.data, include_amber=include_amber,
                       only_ids=set(ids) if ids else None)
        return p.to_dict()

    def rename(self, include_amber: bool = False, ids: list[str] | None = None) -> dict:
        if not self._busy.acquire(blocking=False):
            return {"started": False, "message": "Rennix is still working on the last batch."}
        threading.Thread(target=self._rename, args=(include_amber, ids), daemon=True).start()
        return {"started": True}

    def _rename(self, include_amber: bool, ids: list[str] | None) -> None:
        try:
            items = [self._items[i] for i in self._order if i in self._items]
            plan = build_plan(items, self._formats, self._settings.data, include_amber=include_amber,
                              only_ids=set(ids) if ids else None)
            total = len([o for o in plan.ops if o.kind == "video"])
            if total == 0:
                self._emit("renamed", {"done": 0, "failed": 0, "companions": 0, "errors": [],
                                       "problems": plan.problems, "message": "Nothing to rename."})
                return
            count = {"n": 0}

            def progress(op):
                if op.kind == "video":
                    count["n"] += 1
                    self._emit("rename_progress", {"done": count["n"], "total": total,
                                                   "file_id": op.file_id, "state": op.state})

            label = f"{total} file{'s' if total != 1 else ''}"
            summary = Renamer(self._paths, self._settings.data).execute(plan, progress, label=label)
            done_ids = {o.file_id for o in plan.ops if o.kind == "video"} - {e["file_id"] for e in summary["errors"]}
            for i in done_ids:
                self._items.pop(i, None)
            self._order = [i for i in self._order if i in self._items]
            if summary["done"]:
                self._learned.bump("renamed", summary["done"])
            self._apply_junk_policy()
            summary["problems"] = plan.problems
            summary["removed_ids"] = sorted(done_ids)
            self._emit("renamed", summary)
        except Exception as exc:
            log_event(log, logging.ERROR, "RNX-X001", f"Renaming stopped: {exc}", exc)
            self._emit("error", {"message": friendly("RNX-X001", "Renaming stopped. Nothing was lost; see History.")})
        finally:
            self._busy.release()

    def _apply_junk_policy(self) -> None:
        policy = self._settings.get("leftovers", "junk", "leave")
        if policy == "leave" or not self._junk:
            return
        for j in list(self._junk):
            p = Path(j)
            if not p.exists():
                continue
            try:
                if policy == "recycle":
                    from send2trash import send2trash
                    send2trash(str(p))
                elif policy == "folder":
                    target = p.parent / "_Rennix leftovers"
                    target.mkdir(exist_ok=True)
                    os.rename(p, target / p.name)
            except Exception as exc:
                log_event(log, logging.WARNING, "RNX-R099", f"Couldn't tidy a junk file: {exc}", path=str(p))
        self._junk = []

    def export_plan(self, include_amber: bool = False) -> dict:
        if self._window is None:
            return {"saved": False}
        import webview
        res = self._window.create_file_dialog(webview.SAVE_DIALOG, save_filename="rennix-plan.csv")
        if not res:
            return {"saved": False}
        target = res if isinstance(res, str) else res[0]
        p = build_plan([self._items[i] for i in self._order if i in self._items], self._formats,
                       self._settings.data, include_amber=include_amber)
        with open(target, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["Current path", "New path", "Type", "Action"])
            for o in p.ops:
                w.writerow([o.src, o.dst, o.kind, o.action])
        return {"saved": True, "path": target}

    # ------------------------------------------------------------------ history and recovery
    def history(self) -> list[dict]:
        return Renamer(self._paths, self._settings.data).history()

    def undo(self, batch_id: str, op_ids: list[str] | None = None) -> dict:
        try:
            return Renamer(self._paths, self._settings.data).undo(batch_id, op_ids)
        except (OSError, ValueError) as exc:
            return {"undone": 0, "errors": [{"message": f"Couldn't undo: {exc}"}]}

    def pending_journal(self) -> dict | None:
        j = Renamer(self._paths, self._settings.data).pending_journal()
        if not j:
            return None
        pending = len([o for o in j["ops"] if o["state"] == "pending" and o["kind"] == "video"])
        done = len([o for o in j["ops"] if o["state"] == "done" and o["kind"] == "video"])
        return {"id": j["id"], "pending": pending, "done": done, "created": j.get("created")}

    def recover(self, mode: str = "finish") -> dict:
        return Renamer(self._paths, self._settings.data).recover("reverse" if mode == "reverse" else "finish")

    # ------------------------------------------------------------------ console and diagnostics
    def get_logs(self, last_id: int = 0) -> dict:
        return {"entries": BUFFER.since(int(last_id or 0)), "counts": BUFFER.counts()}

    def clear_logs(self) -> dict:
        BUFFER.clear()
        return {"ok": True}

    def error_codes(self) -> dict:
        return {k: {"what": v[0], "fix": v[1]} for k, v in CODES.items()}

    def log_js(self, level: str, message: str, detail: str = "") -> bool:
        """Errors from the app window end up in the same log as everything else."""
        lvl = {"error": logging.ERROR, "warning": logging.WARNING}.get(level, logging.INFO)
        logging.getLogger("rennix.window").log(lvl, str(message)[:500],
                                               extra={"code": "RNX-W001" if lvl >= logging.ERROR else "",
                                                      "detail": str(detail)[:4000]})
        return True

    def diagnostics_report(self) -> str:
        busy = not self._busy.acquire(blocking=False)
        if not busy:
            self._busy.release()
        return report(__version__, self._settings.data, {
            "MediaInfo": "available" if mediainfo.available() else "not available",
            "Files loaded": len(self._order),
            "Busy": "yes" if busy else "no",
            "Data folder": str(self._paths.data),
        })

    def save_report(self) -> dict:
        if self._window is None:
            return {"saved": False}
        import webview
        res = self._window.create_file_dialog(webview.SAVE_DIALOG, save_filename="rennix-report.txt")
        if not res:
            return {"saved": False}
        target = res if isinstance(res, str) else res[0]
        Path(target).write_text(self.diagnostics_report(), encoding="utf-8")
        return {"saved": True, "path": target}

    # ------------------------------------------------------------------ formats
    def list_formats(self) -> dict:
        out = {}
        for name, f in self._formats.all().items():
            out[name] = {
                "movie": f["movie"], "episode": f["episode"],
                "description": f.get("description", ""), "builtin": f.get("builtin", False),
                "movie_example": render(f["movie"], SAMPLE_FACTS["movie"], ".mkv"),
                "episode_example": render(f["episode"], SAMPLE_FACTS["episode"], ".mkv"),
            }
        return out

    def check_format(self, text: str, kind: str) -> dict:
        kind = "episode" if kind == "episode" else "movie"
        issues = validate(text, kind)
        examples = []
        if not any(i["level"] == "error" for i in issues):
            mine = [self._items[i] for i in self._order if i in self._items
                    and self._items[i].match and (self._items[i].match.kind == "movie") == (kind == "movie")][:4]
            if mine:
                names = {}
                for it in mine:
                    new = render(text, facts_for(it), it.file.path.suffix)
                    examples.append({"old": it.file.name, "new": new})
                    names.setdefault(new.lower(), 0)
                    names[new.lower()] += 1
                if any(v > 1 for v in names.values()):
                    issues.append({"level": "warning",
                                   "message": "Two of your loaded files would get the same name with this format."})
            else:
                samples = [SAMPLE_FACTS[kind]]
                if kind == "episode":
                    samples.append(dict(SAMPLE_FACTS["episode"], season=3, episodes=[1, 2],
                                        show="The Office", show_year=2005, tmdb_id=2316,
                                        episode_titles=["Gay Witch Hunt", "The Convention"], resolution=""))
                else:
                    samples.append(dict(SAMPLE_FACTS["movie"], title="Dune: Part Two", year=2024,
                                        tmdb_id=693134, edition="Extended", resolution="2160p", hdr="Dolby Vision"))
                for f in samples:
                    examples.append({"old": "(example)", "new": render(text, f, ".mkv")})
        try:
            parts = parts_to_json(parse_format(text))
        except FormatError:
            parts = []
        return {"issues": issues, "examples": examples, "parts": parts}

    def format_from_parts(self, parts: list) -> str:
        return format_to_text(parts_from_json(parts))

    def save_format(self, name: str, movie: str | None = None, episode: str | None = None,
                    description: str = "") -> dict:
        name = (name or "").strip()
        if not name:
            return {"ok": False, "message": "Give your format a name."}
        for kind, text in (("movie", movie), ("episode", episode)):
            if text is not None and any(i["level"] == "error" for i in validate(text, kind)):
                return {"ok": False, "message": f"Fix the problems in the {'film' if kind == 'movie' else 'TV'} format first."}
        try:
            self._formats.save(name, movie, episode, description)
        except FormatError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "formats": self.list_formats()}

    def delete_format(self, name: str) -> dict:
        self._formats.delete(name)
        for key in ("movie_format", "episode_format"):
            if self._settings.get("naming", key) == name:
                self._settings.update({"naming": {key: "Plex"}})
        return {"formats": self.list_formats(), "settings": self._settings.data}

    # ------------------------------------------------------------------ misc
    def clear_cache(self) -> dict:
        self._cache.clear()
        return {"ok": True}

    def check_updates(self) -> dict:
        """Look for a newer release on GitHub. Never downloads anything by itself."""
        try:
            import requests
            channel = self._settings.get("updates", "channel", "stable")
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
            resp = requests.get(url, timeout=10, headers={"Accept": "application/vnd.github+json"})
            if resp.status_code != 200:
                return {"available": False, "message": "Couldn't check for updates right now."}
            releases = [r for r in resp.json() if not r.get("draft")]
            if channel != "beta":
                releases = [r for r in releases if not r.get("prerelease")]
            if not releases:
                return {"available": False, "message": "You're on the latest version."}
            latest = releases[0]
            tag = (latest.get("tag_name") or "").lstrip("v")
            if tag and _version_tuple(tag) > _version_tuple(__version__):
                return {"available": True, "version": tag, "url": latest.get("html_url"),
                        "notes": (latest.get("body") or "")[:1200]}
            return {"available": False, "message": "You're on the latest version."}
        except Exception:
            return {"available": False, "message": "Couldn't check for updates right now."}


def _guarded(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as exc:
            log_event(log, logging.ERROR, "RNX-X001", f"{fn.__name__} failed: {exc}", exc,
                      args=[str(a)[:200] for a in args])
            raise
    return wrapper


for _name, _fn in list(vars(Api).items()):
    if callable(_fn) and not _name.startswith("_") and _name not in ("get_logs", "log_js"):
        setattr(Api, _name, _guarded(_fn))


def _version_tuple(v: str) -> tuple:
    import re
    nums = [int(n) for n in re.findall(r"\d+", v.split("-")[0])][:3]
    while len(nums) < 3:
        nums.append(0)
    pre = 0 if "-" in v else 1          # 1.0.0-alpha < 1.0.0
    return (*nums, pre)
