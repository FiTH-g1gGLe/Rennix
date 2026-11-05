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


def main() -> int:
    try:
        import webview
    except ImportError:
        print("Rennix needs the 'pywebview' package. Run: pip install -r requirements.txt")
        return 1

    from .api import Api
    from .config import resource_path

    api = Api()
    index = resource_path("ui", "index.html")
    window = webview.create_window(
        "Rennix", url=str(index), js_api=api, width=1320, height=860,
        min_size=(960, 640), background_color="#0E1014", text_select=False,
    )
    api._window = window
    _install_drop_handler(window, api)
    webview.start(debug=os.environ.get("RENNIX_DEBUG") == "1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
