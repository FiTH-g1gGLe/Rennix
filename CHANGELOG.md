# Changelog

All notable changes to Rennix are listed here.

## 0.1.2-alpha (November 2026)

### Fixed
- The portable version could fail to open with "Failed to resolve Python.Runtime.Loader.Initialize". Windows marks files extracted from a downloaded zip, and the window component refused to load them. Rennix now clears that mark from its own files when it starts.
- If the app window ever can't open, Rennix now shows a clear message with the fix and error code `RNX-W002`, instead of a raw error.

## 0.1.1-alpha (November 2026)

### Added
- 🐞 Console: a live log of everything Rennix does, with filters, search and a one-click **Copy report** for getting help (Ctrl + Shift + L)
- Error codes (like `RNX-R001`) on every problem, each with a plain explanation and what to do
- Detailed log files with rotation, crash capture and interface error capture

## 0.1.0-alpha (November 2026)

First test build.

### Added
- Identification engine: file names, folders, IDs and `.nfo` files, matched against TheMovieDB with year, runtime, episode and country checks
- Green, amber and red confidence with explanations on hover
- Manual matching with poster search; your choices are remembered
- Naming templates for Plex, Jellyfin, Emby, Kodi, Simple and Detailed, plus a custom format editor with live preview
- Subtitles, posters and `.nfo` files renamed with their video
- Move, copy, hardlink and symlink modes
- Crash journal, rename history and undo
- Conflict handling, duplicate warnings, junk-file handling and CSV export of the plan
- Settings, first-run welcome, dark and light themes, tooltips and keyboard shortcuts
- Windows installer and portable builds
