"""Launcher used for the packaged app (PyInstaller) and for running from source."""

from rennix.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
