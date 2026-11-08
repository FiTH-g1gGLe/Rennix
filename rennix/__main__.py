"""Start Rennix: ``python -m rennix`` or the packaged Rennix app."""

from __future__ import annotations

import os
import sys


def _install_drop_handler(window, api) -> None:
    """Files dragged from Explorer/Finder onto the window are added to the list."""
    try:
        from webview.dom import DOMEventHandler
    except ImportError:
        return

    def on_drop(event):
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if paths:
            mode = api._settings.get("general", "default_mode", "auto")
            api.add_paths(paths, mode)

    def on_loaded():
        try:
            doc = window.dom.document
            doc.events.dragenter += DOMEventHandler(lambda e: None, True, True)
            doc.events.dragover += DOMEventHandler(lambda e: None, True, True, debounce=500)
            doc.events.drop += DOMEventHandler(on_drop, True, True)
        except Exception:
            pass

    window.events.loaded += on_loaded


def _unblock_bundled_files() -> int:
    """Remove Windows' "downloaded from the internet" mark from Rennix's own files.

    Windows tags every file extracted from a downloaded zip. The .NET runtime that
    opens the app window then refuses to load Python.Runtime.dll from such files,
    and Rennix fails to start. Deleting the hidden Zone.Identifier stream fixes it.
    Only touches files inside Rennix's own folder.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return 0
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    roots = {base, os.path.dirname(sys.executable)}
    cleared = 0
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if not name.lower().endswith((".dll", ".pyd", ".exe", ".config", ".json")):
                    continue
                try:
                    os.remove(os.path.join(dirpath, name) + ":Zone.Identifier")
                    cleared += 1
                except OSError:
                    pass  # no mark on this file (the usual case)
    return cleared


def _show_start_error(message: str) -> None:
    """A plain Windows message box, used when the app window itself can't open."""
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Rennix couldn't start", 0x10)
            return
    except Exception:
        pass
    print(message, file=sys.stderr)


def main() -> int:
    cleared = _unblock_bundled_files()
    try:
        import webview
    except ImportError:
        print("Rennix needs the 'pywebview' package. Run: pip install -r requirements.txt")
        return 1

    import logging

    from .api import Api
    from .config import resource_path
    from .diagnostics import friendly, log_event

    api = Api()
    log = logging.getLogger("rennix.app")
    if cleared:
        log.info("Removed the 'downloaded from the internet' mark from %d of Rennix's own files", cleared)
    index = resource_path("ui", "index.html")
    window = webview.create_window(
        "Rennix", url=str(index), js_api=api, width=1320, height=860,
        min_size=(960, 640), background_color="#0E1014", text_select=False,
    )
    api._window = window
    _install_drop_handler(window, api)
    try:
        webview.start(debug=os.environ.get("RENNIX_DEBUG") == "1")
    except Exception as exc:
        log_event(log, logging.CRITICAL, "RNX-W002", f"The app window couldn't open: {exc}", exc)
        _show_start_error(
            friendly("RNX-W002") + "\n\n"
            "Try this:\n"
            "• Use the installer (Rennix-Setup) instead of the portable zip, or\n"
            "• Right-click the downloaded zip → Properties → tick Unblock → OK, then extract it again.\n\n"
            f"Details were saved to the log folder:\n{api._paths.logs}"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
