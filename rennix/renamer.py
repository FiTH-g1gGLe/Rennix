"""Safe renaming: plan, check, journal, execute, recover and undo.

Safety rules (from the plan):
* Same-drive moves only change the name; file contents are never rewritten.
* Cross-drive moves copy to a temporary ``.rennix-part`` file, confirm the size
  matches, then swap it in and only then remove the original.
* Nothing is ever overwritten silently; clashes follow the user's policy.
* A journal is written to disk BEFORE a batch starts and updated after every
  file, so a crash or power cut never leaves a half-done batch: on next launch
  Rennix can finish it or reverse it.
* Every rename is recorded in the history and can be undone.
"""

from __future__ import annotations

import datetime as _dt
import errno
import json
import os
import re
import secrets
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .config import atomic_write_json
from .naming import Formats, facts_for, render
from .scanner import IMAGE_EXTS, INFO_EXTS, SUBTITLE_EXTS

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

IS_WINDOWS = os.name == "nt"
MAX_PATH = 259 if IS_WINDOWS else 4000
RES_RANK = {"2160p": 5, "1440p": 4, "1080p": 3, "1080i": 3, "720p": 2, "576p": 1, "480p": 1}


# --------------------------------------------------------------------------- data

@dataclass
class Op:
    id: str
    file_id: str
    src: str
    dst: str
    action: str = "move"          # move | copy | hardlink | symlink
    kind: str = "video"           # video | companion
    state: str = "pending"        # pending | done | failed | undone | skipped
    replace: bool = False         # replace an existing lower-quality file (sent to Recycle Bin)
    error: str = ""
    created_dirs: list = field(default_factory=list)
    replaced_to_trash: bool = False


@dataclass
class Plan:
    ops: list = field(default_factory=list)
    problems: list = field(default_factory=list)   # {file_id, level, message}
    skipped: list = field(default_factory=list)    # {file_id, reason}

    def to_dict(self) -> dict:
        return {"ops": [asdict(o) for o in self.ops], "problems": self.problems,
                "skipped": self.skipped}


def _norm_key(p: Path) -> str:
    s = str(p)
    return s.lower() if IS_WINDOWS or sys.platform == "darwin" else s


def _friendly(exc: BaseException, action: str = "move") -> str:
    if isinstance(exc, FileExistsError):
        return "A file with the new name already exists."
    if isinstance(exc, FileNotFoundError):
        return "The file has moved or been deleted since it was scanned."
    if isinstance(exc, PermissionError):
        return "The file is in use or protected. Close any program using it and try again."
    if isinstance(exc, OSError):
        if exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17:
            if action == "hardlink":
                return "Hardlinks only work when the file and library are on the same drive."
            return "Couldn't move between drives."
        if exc.errno in (errno.ENAMETOOLONG,) or getattr(exc, "winerror", None) == 206:
            return "The new path is too long for this drive."
        if exc.errno == errno.ENOSPC:
            return "Not enough free space on the destination drive."
        if action == "symlink" and IS_WINDOWS:
            return "Windows needs Developer Mode turned on to create symlinks."
        return exc.strerror or str(exc)
    return str(exc)


def _rank(path_or_res: str) -> int:
    for res, rank in RES_RANK.items():
        if res in path_or_res.lower():
            return rank
    return 0


def new_batch_id() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


# --------------------------------------------------------------------------- planning

def build_plan(items: list, formats: Formats, settings: dict, include_amber: bool = False,
               only_ids: set | None = None, existing=os.path.exists) -> Plan:
    """Work out every rename without touching the disk."""
    plan = Plan()
    lib = settings.get("library", {})
    naming = settings.get("naming", {})
    policy = settings.get("conflicts", {}).get("policy", "skip")
    action = lib.get("action", "move")
    sort_into_library = lib.get("sort_into_library", False)
    taken: dict[str, str] = {}          # normalised destination -> file_id
    seen_matches: dict[tuple, str] = {}
    counter = 0

    for it in items:
        fid = it.file.id
        if only_ids is not None and fid not in only_ids:
            continue
        if it.match is None or it.status == "red":
            plan.skipped.append({"file_id": fid, "reason": "No match"})
            continue
        if it.status == "amber" and not include_amber:
            plan.skipped.append({"file_id": fid, "reason": "Needs checking"})
            continue
        m = it.match
        kind = "movie" if m.kind == "movie" else "episode"
        fmt_name = naming.get("movie_format" if kind == "movie" else "episode_format", "Plex")
        fmt = formats.get(fmt_name, kind)
        src = Path(it.file.path)
        ext = src.suffix

        lib_dir = lib.get("movies_dir" if kind == "movie" else "tv_dir") or ""
        if sort_into_library and lib_dir:
            base = Path(lib_dir)
            rel = render(fmt, facts_for(it), ext, MAX_PATH, len(str(base)))
        else:
            base = src.parent
            rel = render(fmt, facts_for(it), ext, MAX_PATH, len(str(base)))
            rel = rel.rsplit("/", 1)[-1]          # renaming in place: file name only
        if not rel:
            plan.problems.append({"file_id": fid, "level": "error",
                                  "message": "The naming format produced an empty name."})
            continue
        dst = base.joinpath(*rel.split("/"))

        # duplicates: two files matching the same film or episode
        key = (m.kind, m.tmdb_id, m.season, tuple(m.episodes), it.parsed.part)
        if key in seen_matches:
            plan.problems.append({"file_id": fid, "level": "warning",
                                  "message": "Duplicate: another file in this batch matched the same "
                                             + ("film." if m.kind == "movie" else "episode.")})
        seen_matches[key] = fid

        if dst == src:
            plan.skipped.append({"file_id": fid, "reason": "Already named correctly"})
            continue

        dst, replace, problem = _resolve_clash(src, dst, fid, taken, policy, existing, it)
        if problem:
            plan.problems.append(problem)
            if dst is None:
                continue
        if len(str(dst)) > MAX_PATH:
            plan.problems.append({"file_id": fid, "level": "error",
                                  "message": "The new path is too long for Windows. Use a shorter format "
                                             "or library folder."})
            continue
        taken[_norm_key(dst)] = fid
        counter += 1
        plan.ops.append(Op(id=f"o{counter}", file_id=fid, src=str(src), dst=str(dst),
                           action=action, replace=replace))

        # companions follow the video
        for c in it.file.companions:
            ext_l = c.path.suffix.lower()
            if ext_l in SUBTITLE_EXTS and not naming.get("rename_subtitles", True):
                continue
            if ext_l in IMAGE_EXTS and not naming.get("rename_images", True):
                continue
            if ext_l in INFO_EXTS and not naming.get("rename_nfo", True):
                continue
            if c.generic:
                if dst.parent == src.parent:
                    continue                      # poster.jpg stays where it is
                cdst = dst.parent / c.suffix
            else:
                cdst = dst.parent / (dst.stem + c.suffix)
            if cdst == c.path or _norm_key(cdst) in taken or existing(str(cdst)):
                continue
            taken[_norm_key(cdst)] = fid
            counter += 1
            plan.ops.append(Op(id=f"o{counter}", file_id=fid, src=str(c.path), dst=str(cdst),
                               action=action, kind="companion"))
    return plan


def _resolve_clash(src: Path, dst: Path, fid: str, taken: dict, policy: str, existing, it):
    """Returns (destination or None, replace flag, problem dict or None)."""
    clash_batch = _norm_key(dst) in taken
    clash_disk = existing(str(dst)) and _norm_key(dst) != _norm_key(src)
    if not clash_batch and not clash_disk:
        return dst, False, None
    if policy == "number":
        stem, ext = dst.stem, dst.suffix
        for n in range(2, 100):
            cand = dst.with_name(f"{stem} ({n}){ext}")
            if _norm_key(cand) not in taken and not existing(str(cand)):
                return cand, False, {"file_id": fid, "level": "info",
                                     "message": f"Name already used, saved as “{cand.name}”."}
    if policy == "replace_if_better" and clash_disk and not clash_batch:
        mine = _rank(it.probe.get("resolution") or it.parsed.resolution or "")
        theirs = _rank(dst.name)
        if mine > theirs and send2trash is not None:
            return dst, True, {"file_id": fid, "level": "info",
                               "message": "Replaces a lower-quality copy (sent to the Recycle Bin)."}
    where = "another file in this batch" if clash_batch else "a file already there"
    return None, False, {"file_id": fid, "level": "conflict",
                         "message": f"Skipped: the new name clashes with {where}."}


# --------------------------------------------------------------------------- execution

class Renamer:
    def __init__(self, paths, settings: dict | None = None):
        self.paths = paths
        self.settings = settings or {}

    # ----------------------------------------------------------------- files
    def _make_parents(self, dst: Path) -> list[str]:
        created = []
        p = dst.parent
        missing = []
        while not p.exists():
            missing.append(p)
            if p.parent == p:
                break
            p = p.parent
        for d in reversed(missing):
            d.mkdir(exist_ok=True)
            created.append(str(d))
        return created

    def _copy_verified(self, src: Path, dst: Path) -> None:
        part = dst.with_name(dst.name + ".rennix-part")
        try:
            shutil.copy2(src, part)
            if part.stat().st_size != src.stat().st_size:
                raise OSError(errno.EIO, "The copy didn't match the original's size")
            if dst.exists():
                raise FileExistsError(str(dst))
            os.rename(part, dst)
        finally:
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass

    def _move(self, src: Path, dst: Path) -> None:
        if IS_WINDOWS:
            try:
                os.rename(src, dst)              # never overwrites on Windows
                return
            except OSError as exc:
                if getattr(exc, "winerror", None) != 17:
                    raise
        else:
            try:
                os.link(src, dst)                # atomic "create only if missing"
                os.unlink(src)
                return
            except FileExistsError:
                raise
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    # filesystems without hardlinks (FAT, some network shares)
                    if dst.exists():
                        raise FileExistsError(str(dst))
                    os.rename(src, dst)
                    return
        # different drive: verified copy, then remove the original
        self._copy_verified(src, dst)
        os.remove(src)

    def _do(self, op: Op) -> None:
        src, dst = Path(op.src), Path(op.dst)
        if not src.exists() and not src.is_symlink():
            raise FileNotFoundError(str(src))
        op.created_dirs = self._make_parents(dst)
        if dst.exists():
            if op.replace and send2trash is not None:
                send2trash(str(dst))
                op.replaced_to_trash = True
            else:
                raise FileExistsError(str(dst))
        if op.action == "move":
            self._move(src, dst)
        elif op.action == "copy":
            self._copy_verified(src, dst)
        elif op.action == "hardlink":
            os.link(src, dst)
        elif op.action == "symlink":
            os.symlink(src, dst)
        else:
            raise ValueError(f"Unknown action {op.action}")

    # ----------------------------------------------------------------- batches
    def _journal(self, batch: dict) -> None:
        atomic_write_json(self.paths.journal_file, batch)

    def execute(self, plan: Plan, progress: Callable[[Op], None] | None = None,
                label: str = "") -> dict:
        """Run a plan. Returns a summary and records the batch for Undo."""
        batch = {
            "id": new_batch_id(),
            "created": _dt.datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "ops": [asdict(o) for o in plan.ops],
            "removed_dirs": [],
        }
        self._journal(batch)
        self._run(batch, progress)
        return self._close(batch)

    def _run(self, batch: dict, progress=None) -> None:
        for i, od in enumerate(batch["ops"]):
            if od["state"] != "pending":
                continue
            op = Op(**od)
            # a companion follows its video: if the video failed, leave it alone
            if op.kind == "companion":
                video = next((o for o in batch["ops"] if o["file_id"] == op.file_id and o["kind"] == "video"), None)
                if video and video["state"] != "done":
                    op.state, op.error = "skipped", "Its video wasn't renamed."
                    batch["ops"][i] = asdict(op)
                    self._journal(batch)
                    continue
            try:
                self._do(op)
                op.state = "done"
            except (OSError, ValueError) as exc:
                op.state, op.error = "failed", _friendly(exc, op.action)
            batch["ops"][i] = asdict(op)
            self._journal(batch)
            if progress:
                try:
                    progress(op)
                except Exception:
                    pass

    def _close(self, batch: dict) -> dict:
        if self.settings.get("library", {}).get("tidy_empty_folders", True):
            batch["removed_dirs"] = self._tidy_sources(batch)
        done = [o for o in batch["ops"] if o["state"] == "done"]
        failed = [o for o in batch["ops"] if o["state"] == "failed"]
        batch["finished"] = _dt.datetime.now().isoformat(timespec="seconds")
        batch["summary"] = {
            "done": len([o for o in done if o["kind"] == "video"]),
            "companions": len([o for o in done if o["kind"] == "companion"]),
            "failed": len(failed),
        }
        if done:
            atomic_write_json(self.paths.history / f"{batch['id']}.json", batch)
        try:
            self.paths.journal_file.unlink()
        except FileNotFoundError:
            pass
        return {"batch_id": batch["id"], **batch["summary"],
                "errors": [{"file_id": o["file_id"], "src": o["src"], "message": o["error"]} for o in failed]}

    def _tidy_sources(self, batch: dict) -> list[str]:
        """Remove folders a move left completely empty (never anything with files in)."""
        removed = []
        dirs = sorted({str(Path(o["src"]).parent) for o in batch["ops"]
                       if o["state"] == "done" and o["action"] == "move"}, key=len, reverse=True)
        for d in dirs:
            p = Path(d)
            try:
                if p.exists() and not any(p.iterdir()):
                    p.rmdir()
                    removed.append(d)
            except OSError:
                pass
        return removed

    # ----------------------------------------------------------------- recovery
    def pending_journal(self) -> dict | None:
        try:
            return json.loads(self.paths.journal_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def recover(self, mode: str = "finish") -> dict:
        """After a crash: work out what really happened, then finish or reverse."""
        batch = self.pending_journal()
        if not batch:
            return {"batch_id": None, "done": 0, "companions": 0, "failed": 0, "errors": []}
        for od in batch["ops"]:
            if od["state"] != "pending":
                continue
            src, dst = Path(od["src"]), Path(od["dst"])
            part = dst.with_name(dst.name + ".rennix-part")
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass
            if od["action"] == "move":
                if dst.exists() and not src.exists():
                    od["state"] = "done"
                elif dst.exists() and src.exists():
                    try:
                        if os.path.samefile(src, dst):      # link made, unlink not yet
                            os.unlink(src)
                            od["state"] = "done"
                    except OSError:
                        pass
            elif dst.exists() and src.exists():
                od["state"] = "done"
        self._journal(batch)
        if mode == "finish":
            self._run(batch)
            return self._close(batch)
        summary = self._close(batch)
        if summary.get("batch_id") and summary["done"] + summary["companions"] > 0:
            return self.undo(batch["id"])
        return summary

    # ----------------------------------------------------------------- history and undo
    def history(self) -> list[dict]:
        out = []
        keep_days = self.settings.get("safety", {}).get("history_days", 365)
        cutoff = None
        if keep_days:
            cutoff = _dt.datetime.now() - _dt.timedelta(days=keep_days)
        for f in sorted(self.paths.history.glob("*.json"), reverse=True):
            try:
                b = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            created = b.get("created", "")
            if cutoff and created:
                try:
                    if _dt.datetime.fromisoformat(created) < cutoff:
                        f.unlink()
                        continue
                except ValueError:
                    pass
            vids = [o for o in b["ops"] if o["kind"] == "video"]
            out.append({
                "id": b["id"], "created": created, "label": b.get("label", ""),
                "count": len([o for o in vids if o["state"] == "done"]),
                "undone": len([o for o in vids if o["state"] == "undone"]),
                "ops": [{k: o[k] for k in ("id", "file_id", "src", "dst", "kind", "state", "action")}
                        for o in b["ops"]],
            })
        return out

    def undo(self, batch_id: str, op_ids: list[str] | None = None) -> dict:
        """Reverse a batch (or some of its files). Originals go back where they were."""
        if not re.fullmatch(r"[0-9A-Za-z\-]+", batch_id or ""):
            raise ValueError("Bad batch id")
        f = self.paths.history / f"{batch_id}.json"
        batch = json.loads(f.read_text(encoding="utf-8"))
        wanted = set(op_ids) if op_ids else None
        if wanted:
            # undoing a video also undoes its companions
            fids = {o["file_id"] for o in batch["ops"] if o["id"] in wanted}
            wanted |= {o["id"] for o in batch["ops"] if o["file_id"] in fids}
        for d in batch.get("removed_dirs", []):
            Path(d).mkdir(parents=True, exist_ok=True)
        undone, errors = 0, []
        for od in reversed(batch["ops"]):
            if od["state"] != "done" or (wanted and od["id"] not in wanted):
                continue
            src, dst = Path(od["src"]), Path(od["dst"])
            try:
                if od["action"] == "move":
                    if src.exists():
                        raise FileExistsError(str(src))
                    src.parent.mkdir(parents=True, exist_ok=True)
                    self._move(dst, src)
                else:  # copy, hardlink, symlink: the original never moved; remove what we made
                    if dst.is_symlink() or dst.exists():
                        os.remove(dst)
                od["state"] = "undone"
                undone += 1
                for d in reversed(od.get("created_dirs") or []):
                    try:
                        Path(d).rmdir()
                    except OSError:
                        pass
            except OSError as exc:
                errors.append({"file_id": od["file_id"], "src": od["dst"],
                               "message": "Couldn't undo: " + _friendly(exc, od["action"])})
        atomic_write_json(f, batch)
        return {"batch_id": batch_id, "undone": undone, "errors": errors}
