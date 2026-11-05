"""Error codes, logs and the diagnostic report."""

import errno
import logging

from rennix import renamer as rmod
from rennix.diagnostics import BUFFER, CODES, friendly, log_event, report, setup_logging
from rennix.tmdb import TMDbError


def test_every_code_has_a_message_and_a_fix():
    for code, (what, fix) in CODES.items():
        assert code.startswith("RNX-") and len(code) == 8
        assert what.endswith(".") and fix.endswith(".")


def test_friendly_messages_end_with_the_code():
    assert friendly("RNX-R001").endswith("(RNX-R001)")
    assert TMDbError("x", code="RNX-T002").friendly.endswith("(RNX-T002)")


def test_os_errors_map_to_codes():
    assert rmod._error_code(FileExistsError()) == "RNX-R002"
    assert rmod._error_code(FileNotFoundError()) == "RNX-R003"
    assert rmod._error_code(PermissionError()) == "RNX-R001"
    assert rmod._error_code(OSError(errno.EXDEV, "x"), "hardlink") == "RNX-R004"
    assert rmod._error_code(OSError(errno.ENOSPC, "x")) == "RNX-R006"
    assert rmod._error_code(OSError(errno.ENAMETOOLONG, "x")) == "RNX-R005"
    assert rmod._error_code(OSError(errno.EIO, "The copy didn't match the original's size")) == "RNX-R008"
    assert rmod._error_code(ValueError()) == "RNX-X001"


def test_log_buffer_and_report(tmp_path):
    setup_logging(tmp_path)
    BUFFER.clear()
    log = logging.getLogger("rennix.test")
    try:
        raise PermissionError("locked")
    except PermissionError as exc:
        log_event(log, logging.ERROR, "RNX-R001", exc=exc, src="C:/a.mkv", dst="C:/b.mkv")
    entries = BUFFER.since(0)
    assert entries[-1]["code"] == "RNX-R001"
    assert entries[-1]["ctx"]["src"] == "C:/a.mkv"
    assert "PermissionError" in entries[-1]["detail"]
    assert BUFFER.counts()["errors"] >= 1
    text = report("0.1.0", {"tmdb": {"token": "secret-token"}, "library": {"action": "move"}})
    assert "RNX-R001" in text and "secret-token" not in text and "(set, hidden)" in text
    assert (tmp_path / "rennix.log").read_text(encoding="utf-8").count("RNX-R001") >= 1
