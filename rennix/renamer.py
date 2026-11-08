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
import logging
import os
import re
import secrets
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .config import atomic_write_json
from .diagnostics import friendly, log_event
from .naming import Formats, facts_for, render
from .scanner import IMAGE_EXTS, INFO_EXTS, SUBTITLE_EXTS

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

log = logging.getLogger("rennix.renamer")
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
    code: str = ""
    created_dirs: list = field(default_factory=list)
    replaced_to_trash: bool = False
    payload: dict = field(default_factory=dict)   # metadata: what to save and how to undo it


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


def _error_code(exc: BaseException, action: str = "move") -> str:
    """Map an operating-system error to a Rennix error code."""
    if isinstance(exc, FileExistsError):
        return "RNX-R002"
    if isinstance(exc, FileNotFoundError):
        return "RNX-R003"
    if isinstance(exc, PermissionError):
        return "RNX-R007" if action == "symlink" and IS_WINDOWS else "RNX-R001"
    if isinstance(exc, OSError):
        win = getattr(exc, "winerror", None)
        if exc.errno == errno.EXDEV or win == 17:
            return "RNX-R004" if action == "hardlink" else "RNX-R009"
        if exc.errno == errno.ENAMETOOLONG or win == 206:
            return "RNX-R005"
        if exc.errno == errno.ENOSPC or win == 112:
            return "RNX-R006"
        if exc.errno == errno.EIO and "size" in str(exc):
            return "RNX-R008"
        if win in (32, 33):              # sharing / lock violation
            return "RNX-R001"
        if action == "symlink" and IS_WINDOWS:
            return "RNX-R007"
        return "RNX-R099"
    return "RNX-X001"


def _friendly(exc: BaseException, action: str = "move") -> str:
    return friendly(_error_code(exc, action))


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b) and str(a).lower() == str(b).lower()
    except OSError:
        return False


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

        if str(dst) == str(src):   # exact, so case-only changes still happen on Windows
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
            case_only = _norm_key(cdst) == _norm_key(c.path)
            if str(cdst) == str(c.path) or _norm_key(cdst) in taken or (existing(str(cdst)) and not case_only):
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
    default_tag_writer = None

    def __init__(self, paths, settings: dict | None = None):
        self.paths = paths
        self.settings = settings or {}
        self.tag_writer = Renamer.default_tag_writer   # tests swap in a stand-in for mkvpropedit / mutagen

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
        if dst.exists() and _same_file(src, dst):
            # only the letter case changes (the matrix.mkv -> The Matrix.mkv) on a
            # case-insensitive drive: a plain rename handles it
            os.rename(src, dst)
            return
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
            # companions and metadata follow their video: if the video failed, leave them alone
            if op.kind != "video" and not self._video_ok(batch, op.payload.get("file_ids") or [op.file_id],
                                                         op.payload.get("videos")):
                op.state, op.error = "skipped", "Its video wasn't renamed."
                batch["ops"][i] = asdict(op)
                self._journal(batch)
                continue
            if op.kind in ("meta", "tags"):
                self._do_meta(batch, i, op)
                continue
            try:
                self._do(op)
                op.state = "done"
                log.info("%s: %s → %s", op.action, Path(op.src).name, op.dst,
                         extra={"ctx": {"op": op.id, "file": op.file_id}})
            except (OSError, ValueError) as exc:
                op.code = _error_code(exc, op.action)
                op.state, op.error = "failed", friendly(op.code)
                log_event(log, logging.ERROR, op.code, f"Couldn't {op.action} a file: {exc}", exc,
                          src=op.src, dst=op.dst, action=op.action, errno=getattr(exc, "errno", None),
                          winerror=getattr(exc, "winerror", None))
            batch["ops"][i] = asdict(op)
            self._journal(batch)
            if progress:
                try:
                    progress(op)
                except Exception:
                    pass

    @staticmethod
    def _video_ok(batch: dict, file_ids: list, videos: list | None = None) -> bool:
        """True if at least one of these files is in place: renamed, or already named correctly.

        For metadata, the video must really be at its final path, so nothing is ever
        saved into a folder whose film has gone."""
        for n, fid in enumerate(file_ids):
            video = next((o for o in batch["ops"] if o["file_id"] == fid and o["kind"] == "video"), None)
            if video is None or video["state"] == "done":
                if videos is None or (n < len(videos) and os.path.exists(videos[n])):
                    return True
        return False

    def _backup_dir(self, batch: dict) -> Path:
        return Path(self.paths.backups) / batch["id"]

    def _do_meta(self, batch: dict, i: int, op: Op) -> None:
        from . import embed, metadata
        try:
            if op.kind == "meta":
                metadata.place(op, self._backup_dir(batch))
                log.info("Saved %s: %s", metadata.LABELS.get(op.payload.get("what"), "file"), op.dst)
            else:
                p = op.payload

                def ready(record):
                    # the backup exists; record it in the crash journal before the file is touched
                    p["record"] = record
                    batch["ops"][i] = asdict(op)
                    self._journal(batch)

                cover = Path(p["cover"]) if p.get("cover") else None
                p["record"] = embed.write(op.dst, p.get("tags"), cover, self._backup_dir(batch), op.id,
                                          safe_copy=bool(p.get("safe_copy")), replace_cover=bool(p.get("replace")),
                                          on_ready=ready, _writer=self.tag_writer)
                log.info("Tags written into %s (%s)", Path(op.dst).name, p["record"]["mode"])
            op.state = "done"
        except embed.EmbedError as exc:
            op.state, op.code, op.error = "failed", exc.code, friendly(exc.code, exc.message)
            op.payload.pop("record", None)
            log_event(log, logging.ERROR, exc.code, exc.message, file=op.dst)
        except FileExistsError:
            op.state, op.code, op.error = "skipped", "RNX-M011", friendly("RNX-M011")
            log.info("Kept existing %s", op.dst)
        except OSError as exc:
            op.code = _error_code(exc, "copy")
            op.state, op.error = "failed", friendly(op.code)
            log_event(log, logging.ERROR, op.code, f"Couldn't save {op.dst}: {exc}", exc)
        batch["ops"][i] = asdict(op)
        self._journal(batch)

    def _close(self, batch: dict) -> dict:
        if self.settings.get("library", {}).get("tidy_empty_folders", True):
            batch["removed_dirs"] = self._tidy_sources(batch)
        done = [o for o in batch["ops"] if o["state"] == "done"]
        failed = [o for o in batch["ops"] if o["state"] == "failed"]
        batch["finished"] = _dt.datetime.now().isoformat(timespec="seconds")
        meta_done = [o for o in done if o["kind"] in ("meta", "tags")]
        batch["summary"] = {
            "done": len([o for o in done if o["kind"] == "video"]),
            "companions": len([o for o in done if o["kind"] == "companion"]),
            "failed": len(failed),
            "meta": len({f for o in meta_done for f in (o.get("payload", {}).get("file_ids") or [o["file_id"]])}),
            "meta_items": len(meta_done),
        }
        if done:
            atomic_write_json(self.paths.history / f"{batch['id']}.json", batch)
        else:
            shutil.rmtree(self._backup_dir(batch), ignore_errors=True)
        try:
            self.paths.journal_file.unlink()
        except FileNotFoundError:
            pass
        log.info("Batch %s finished: %s renamed, %s extra files, %s failed", batch["id"],
                 batch["summary"]["done"], batch["summary"]["companions"], batch["summary"]["failed"])
        return {"batch_id": batch["id"], **batch["summary"],
                "errors": [{"file_id": o["file_id"], "src": o["src"] or o["dst"], "message": o["error"],
                            "code": o.get("code", "")} for o in failed]}

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
        log_event(log, logging.WARNING, "RNX-U002", f"Recovering an interrupted batch ({mode})", batch=batch["id"])
        from . import embed, metadata
        for od in batch["ops"]:
            if od["state"] != "pending":
                continue
            if od["kind"] == "meta":
                metadata.cleanup_part(od)
                if metadata.is_ours(od):
                    od["state"] = "done"
                continue
            if od["kind"] == "tags":
                record = od.get("payload", {}).get("record")
                if record:
                    try:
                        if embed.recover(od["dst"], record) == "done":
                            od["state"] = "done"
                        else:
                            od["payload"].pop("record", None)
                            log_event(log, logging.WARNING, "RNX-U003", file=od["dst"])
                    except (OSError, embed.EmbedError) as exc:
                        log_event(log, logging.ERROR, "RNX-U001", f"Couldn't repair {od['dst']}: {exc}", exc)
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
                        shutil.rmtree(Path(self.paths.backups) / b["id"], ignore_errors=True)
                        continue
                except ValueError:
                    pass
            vids = [o for o in b["ops"] if o["kind"] == "video"]
            metas = [o for o in b["ops"] if o["kind"] in ("meta", "tags")]
            out.append({
                "id": b["id"], "created": created, "label": b.get("label", ""),
                "count": len([o for o in vids if o["state"] == "done"]),
                "undone": len([o for o in vids if o["state"] == "undone"]),
                "meta": len({f for o in metas if o["state"] == "done"
                             for f in ((o.get("payload") or {}).get("file_ids") or [o["file_id"]])}),
                "meta_undone": len({f for o in metas if o["state"] == "undone"
                                    for f in ((o.get("payload") or {}).get("file_ids") or [o["file_id"]])}),
                "can_undo": any(o["state"] == "done" for o in b["ops"]),
                "ops": [{**{k: o[k] for k in ("id", "file_id", "src", "dst", "kind", "state", "action")},
                         "what": (o.get("payload") or {}).get("what", "")}
                        for o in b["ops"]],
            })
        return out

    def _undo_meta(self, od: dict, errors: list) -> bool:
        from . import embed, metadata
        try:
            if od["kind"] == "meta":
                metadata.remove(od)
            else:
                embed.undo(od["dst"], od["payload"]["record"])
            return True
        except embed.EmbedError as exc:
            msg = friendly(exc.code, exc.message)
        except (OSError, KeyError) as exc:
            msg = friendly("RNX-U001", str(exc))
        log_event(log, logging.WARNING, "RNX-U001", f"Couldn't undo metadata: {msg}", file=od["dst"])
        errors.append({"file_id": od["file_id"], "src": od["dst"], "code": "RNX-U001", "message": msg})
        return False

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
        undone, videos, errors = 0, 0, []
        for od in reversed(batch["ops"]):
            if od["state"] != "done" or (wanted and od["id"] not in wanted):
                continue
            if od["kind"] in ("meta", "tags"):
                if self._undo_meta(od, errors):
                    od["state"] = "undone"
                    undone += 1
                continue
            src, dst = Path(od["src"]), Path(od["dst"])
            try:
                if od["action"] == "move":
                    if src.exists() and _same_file(src, dst):
                        os.rename(dst, src)         # undo a case-only rename
                        od["state"] = "undone"
                        undone += 1
                        videos += od["kind"] == "video"
                        continue
                    if src.exists():
                        raise FileExistsError(str(src))
                    src.parent.mkdir(parents=True, exist_ok=True)
                    self._move(dst, src)
                else:  # copy, hardlink, symlink: the original never moved; remove what we made
                    if dst.is_symlink() or dst.exists():
                        os.remove(dst)
                od["state"] = "undone"
                undone += 1
                videos += od["kind"] == "video"
                for d in reversed(od.get("created_dirs") or []):
                    try:
                        Path(d).rmdir()
                    except OSError:
                        pass
            except OSError as exc:
                code = _error_code(exc, od["action"])
                log_event(log, logging.ERROR, "RNX-U001", f"Couldn't put a file back: {exc}", exc,
                          src=od["dst"], back_to=od["src"], cause=code)
                errors.append({"file_id": od["file_id"], "src": od["dst"], "code": "RNX-U001",
                               "message": friendly("RNX-U001", "Couldn't put a file back: " + friendly(code))})
        atomic_write_json(f, batch)
        if not any(o["state"] == "done" for o in batch["ops"]):
            shutil.rmtree(self._backup_dir(batch), ignore_errors=True)
        log.info("Undo of batch %s: %s file(s) put back, %s problem(s)", batch_id, undone, len(errors))
        return {"batch_id": batch_id, "undone": undone, "videos": videos, "errors": errors}
