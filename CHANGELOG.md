# Changelog

All notable changes to Rennix are listed here.

## 0.2.1-alpha (November 2026)

### Fixed
- Several genres, writers, directors or actors are now written as one list ("Action, Adventure, Fantasy"), so players like VLC show them all instead of just one.

## 0.2.0-alpha (November 2026)

Version 2: metadata. Everything is optional and off until you turn it on.

### Added
- 📝 **Save metadata** switch in the bottom bar and in Settings → Metadata, with a switch for each item.
- Next to the video: poster, logo, `.nfo` info file, season posters and episode thumbnails, named to suit Plex, Jellyfin, Emby or Kodi.
- Inside the video: tags (title, year, plot, genres, director, cast) and an embedded cover, for MKV (with mkvpropedit, included) and MP4 (with mutagen).
- Safety for tags: the bytes that may change are backed up first; the video data is fingerprinted before and checked after; any problem puts the file back exactly as it was. MP4 files never have their video data shifted in place.
- **Maximum safety** option: always write to a copy, check it, then swap it in.
- Files that are already named correctly get metadata too, without being renamed.
- Undo removes saved files and restores the original tags exactly; existing posters and `.nfo` files are kept unless you turn on **Replace existing**.
- "Date modified" is kept the same when tags are written.
- New error codes `RNX-M001` to `RNX-M011` and `RNX-U003`.

## 0.1.5-alpha (November 2026)

### Fixed
- Rename history now reads clearly after an undo ("3 files renamed, then put back") instead of "0 files renamed · 3 undone".
- Undone files no longer count towards "files renamed so far".

## 0.1.4-alpha (November 2026)

### Fixed
- `{hdr}` now detects HDR10, HDR10+, HLG and Dolby Vision more reliably from the video itself, and still falls back to the file name.
- New option `{hdr:sdr}` writes **SDR** for normal video instead of leaving it empty.
- The format editor explains when `{hdr}` is empty because none of your loaded files are HDR, and adds an example HDR film to the preview so you can see what it does.

## 0.1.3-alpha (November 2026)

### Added
- 👀 Quick preview: rest the mouse on a file to see its poster, plot, runtime, rating, genres, why it matched, and exactly where it will go. Can be turned off in Settings → General.
- New views above the list: ☰ **List**, 🖼️ **Posters** (a poster wall to spot wrong matches at a glance) and 📁 **Folders** (how your library will look after renaming). Rennix remembers your choice.

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
