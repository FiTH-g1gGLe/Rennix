"""Error codes, logging and the diagnostic report.

Every problem Rennix can hit has a short code like ``RNX-R001``. The code is
shown next to the friendly message in the app, written to the log file with
full details, and listed in the console, so any error can be traced exactly.

Code groups:
  RNX-Txxx  TheMovieDB lookups      RNX-Sxxx  scanning folders
  RNX-Ixxx  identification          RNX-Rxxx  renaming
  RNX-Uxxx  undo and recovery       RNX-Fxxx  naming formats
  RNX-Cxxx  settings and storage    RNX-Wxxx  the app window
  RNX-Xxxx  unexpected (a bug: please report it)
"""

from __future__ import annotations

import collections
import datetime as _dt
import json
import logging
import logging.handlers
import platform
import sys
import threading
import traceback
from typing import Any

CODES: dict[str, tuple[str, str]] = {
    # code: (what happened, what to do)
    "RNX-T001": ("Couldn't reach TheMovieDB.", "Check your internet connection, then try again."),
    "RNX-T002": ("TheMovieDB didn't accept the key.", "Check the key in Settings → Matching, or remove it to use Rennix's own."),
    "RNX-T003": ("TheMovieDB has no entry for that.", "Try searching by title instead."),
    "RNX-T004": ("TheMovieDB asked Rennix to slow down.", "Rennix waits and retries automatically."),
    "RNX-T005": ("TheMovieDB had a server problem.", "Usually temporary. Try again in a few minutes."),
    "RNX-T006": ("Offline mode is on and this hasn't been looked up before.", "Turn off offline mode in Settings → Network."),
    "RNX-T007": ("No TheMovieDB key is available.", "Add one in Settings → Matching."),
    "RNX-T008": ("TheMovieDB sent an unexpected reply.", "Try again. If it keeps happening, copy the report for support."),
    "RNX-S001": ("A file or folder couldn't be found.", "It may have been moved, renamed or disconnected."),
    "RNX-S002": ("A folder couldn't be read.", "Check you have permission to open it."),
    "RNX-S003": ("A video's details couldn't be read.", "The file may be damaged or still downloading."),
    "RNX-I001": ("A file couldn't be identified because of an unexpected problem.", "Search for it manually. Please copy the report for support."),
    "RNX-I002": ("No title could be found in the file name.", "Search for it manually."),
    "RNX-I003": ("Nothing on TheMovieDB matched.", "Search for it manually with a different spelling."),
    "RNX-I004": ("That episode number doesn't exist in the matched show.", "Check the season and episode, or pick a different show."),
    "RNX-R001": ("The file is in use or protected.", "Close any program using it (a media player or Plex scan) and try again."),
    "RNX-R002": ("A file with the new name already exists.", "Change the conflict setting, or remove the other file."),
    "RNX-R003": ("The file has moved or been deleted since it was scanned.", "Scan the folder again."),
    "RNX-R004": ("Hardlinks only work when the file and library are on the same drive.", "Use Move or Copy, or pick a library on the same drive."),
    "RNX-R005": ("The new path is too long for this drive.", "Use a shorter format or a library folder nearer the top of the drive."),
    "RNX-R006": ("Not enough free space on the destination drive.", "Free some space or choose another library folder."),
    "RNX-R007": ("Windows needs Developer Mode turned on to create symlinks.", "Use Hardlink instead, or turn on Developer Mode."),
    "RNX-R008": ("The copy didn't match the original's size, so it was cancelled.", "The original is untouched. Check the destination drive."),
    "RNX-R009": ("Couldn't move between drives.", "Check both drives are connected and writable."),
    "RNX-R099": ("The file system refused the rename.", "Copy the report for support."),
    "RNX-U001": ("A file couldn't be put back.", "The original location may be in use or taken by another file."),
    "RNX-U002": ("Rennix was interrupted while renaming.", "Choose Finish or Put everything back from the banner."),
    "RNX-F001": ("The naming format has a problem.", "See the message in the format editor."),
    "RNX-C001": ("Settings couldn't be read, so defaults are in use.", "A copy of the damaged file was kept for support."),
    "RNX-C002": ("A Rennix data file couldn't be saved.", "Check there's free space and the drive is writable."),
    "RNX-W001": ("Something went wrong in the app window.", "Copy the report for support."),
    "RNX-W002": ("The app window couldn't open.", "Use the installer, or unblock the downloaded zip (Properties → Unblock) and extract it again."),
    "RNX-X001": ("Unexpected error. This is a bug.", "Please copy the report and send it so it can be fixed."),
}


def describe(code: str) -> tuple[str, str]:
    return CODES.get(code, CODES["RNX-X001"])


def friendly(code: str, message: str = "") -> str:
    """Message for the app: 'What happened. (RNX-R001)'."""
    what = message or describe(code)[0]
    return f"{what} ({code})"


# --------------------------------------------------------------------------- log buffer

class LogBuffer(logging.Handler):
    """Keeps the most recent log entries in memory for the in-app console."""

    def __init__(self, size: int = 3000):
        super().__init__(level=logging.DEBUG)
        self.entries: collections.deque = collections.deque(maxlen=size)
        self.counter = 0
        self.lock_ = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            detail = getattr(record, "detail", "") or ""
            if record.exc_info:
                detail = (detail + "\n" if detail else "") + "".join(traceback.format_exception(*record.exc_info)).strip()
            ctx = getattr(record, "ctx", None)
            with self.lock_:
                self.counter += 1
                self.entries.append({
                    "id": self.counter,
                    "time": _dt.datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3],
                    "level": record.levelname,
                    "code": getattr(record, "code", "") or "",
                    "where": record.name.replace("rennix.", "") if record.name != "rennix" else "app",
                    "message": record.getMessage(),
                    "detail": detail,
                    "ctx": ctx or {},
                })
        except Exception:
            pass

    def since(self, last_id: int = 0, limit: int = 500) -> list[dict]:
        with self.lock_:
            out = [e for e in self.entries if e["id"] > last_id]
        return out[-limit:]

    def counts(self) -> dict:
        with self.lock_:
            c = collections.Counter(e["level"] for e in self.entries)
        return {"errors": c.get("ERROR", 0) + c.get("CRITICAL", 0), "warnings": c.get("WARNING", 0)}

    def clear(self) -> None:
        with self.lock_:
            self.entries.clear()


class _FileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        code = getattr(record, "code", "")
        ctx = getattr(record, "ctx", None)
        line = f"{self.formatTime(record, '%Y-%m-%d %H:%M:%S')} {record.levelname:<7} "
        line += f"[{code}] " if code else ""
        line += f"{record.name}: {record.getMessage()}"
        if ctx:
            line += " | " + json.dumps(ctx, default=str, ensure_ascii=False)
        detail = getattr(record, "detail", "")
        if detail:
            line += "\n    " + str(detail).replace("\n", "\n    ")
        if record.exc_info:
            line += "\n    " + "".join(traceback.format_exception(*record.exc_info)).strip().replace("\n", "\n    ")
        return line


BUFFER = LogBuffer()
_configured = False


def setup_logging(log_dir) -> None:
    """Log to the console buffer and to rennix.log (rotating, 5 files of 2 MB)."""
    global _configured
    root = logging.getLogger("rennix")
    root.setLevel(logging.DEBUG)
    if BUFFER not in root.handlers:
        root.addHandler(BUFFER)
    if log_dir is not None and not _configured:
        try:
            fh = logging.handlers.RotatingFileHandler(
                str(log_dir / "rennix.log"), maxBytes=2_000_000, backupCount=5, encoding="utf-8")
            fh.setLevel(logging.INFO)
            fh.setFormatter(_FileFormatter())
            root.addHandler(fh)
            _configured = True
        except OSError:
            pass
    _install_crash_hooks()


def _install_crash_hooks() -> None:
    log = logging.getLogger("rennix")

    def excepthook(exc_type, exc, tb):
        log.critical("Unexpected crash: %s", exc, exc_info=(exc_type, exc, tb), extra={"code": "RNX-X001"})

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        log.critical("Unexpected error in background task: %s", args.exc_value,
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback), extra={"code": "RNX-X001"})

    sys.excepthook = excepthook
    threading.excepthook = thread_hook


def log_event(logger: logging.Logger, level: int, code: str, message: str = "", exc: BaseException | None = None,
              **ctx: Any) -> None:
    """Log with a code and context, e.g. log_event(log, logging.ERROR, "RNX-R001", src=..., dst=...)."""
    msg = message or describe(code)[0]
    exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else None
    logger.log(level, msg, exc_info=exc_info, extra={"code": code, "ctx": ctx})


def report(version: str, settings: dict | None = None, extra: dict | None = None, lines: int = 300) -> str:
    """A plain-text diagnostic report to paste to Claude or attach to a bug report.

    Includes versions, system, settings (keys hidden) and recent log entries.
    """
    safe = json.loads(json.dumps(settings or {}, default=str))
    if isinstance(safe.get("tmdb"), dict) and safe["tmdb"].get("token"):
        safe["tmdb"]["token"] = "(set, hidden)"
    out = [
        "=== Rennix diagnostic report ===",
        f"Rennix version : {version}",
        f"Created        : {_dt.datetime.now().isoformat(timespec='seconds')}",
        f"System         : {platform.platform()}",
        f"Python         : {sys.version.split()[0]} ({'packaged app' if getattr(sys, 'frozen', False) else 'from source'})",
    ]
    for k, v in (extra or {}).items():
        out.append(f"{k:<15}: {v}")
    c = BUFFER.counts()
    out.append(f"Log summary    : {c['errors']} error(s), {c['warnings']} warning(s)")
    out += ["", "--- Settings ---", json.dumps(safe, indent=2, ensure_ascii=False), "", "--- Recent log ---"]
    for e in BUFFER.since(0, lines):
        line = f"{e['time']} {e['level']:<7} " + (f"[{e['code']}] " if e["code"] else "") + f"{e['where']}: {e['message']}"
        if e["ctx"]:
            line += " | " + json.dumps(e["ctx"], default=str, ensure_ascii=False)
        out.append(line)
        if e["detail"]:
            out.append("    " + e["detail"].replace("\n", "\n    "))
    out.append("=== end of report ===")
    return "\n".join(out)
