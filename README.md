# Rennix

Rename and organise your movies and TV shows.

Rennix is a free desktop app for Windows, Mac and Linux. Drop in a messy folder of films or episodes, and it works out what each file is, shows you a preview of the new names, and renames and sorts everything in one click. Nothing changes on disk until you've checked the preview, and every rename can be undone.

> **Status:** early alpha (0.1.0). The core works end to end; it's being tested on real libraries before a public release.

## What it does

- **Identifies** movies and TV episodes from messy names, folders, IDs and `.nfo` files, matched against [TheMovieDB](https://www.themoviedb.org/)
- **Shows how sure it is**: green (confident), amber (please check), red (no match), with the reasons on hover
- **Never guesses silently**: a wrong confident match is treated as the worst possible outcome
- **Manual override** for any file, with poster search
- **Naming templates** for Plex, Jellyfin, Emby and Kodi, plus a custom format editor with live preview
- **Renames subtitles, posters and `.nfo` files** along with their video
- **Move, copy, hardlink or symlink** into your library (hardlinks keep torrents seeding)
- **Crash-safe**: a journal means a power cut never leaves a half-done batch
- **Full undo history**

## Running it from source

You need Python 3.11 or newer.

```bash
pip install -r requirements.txt
python run_rennix.py
```

Windows builds (installer and portable zip) are produced automatically by GitHub Actions on every push; see the **Actions** tab.

## TheMovieDB key

Official builds include a key from the `TMDB_READ_TOKEN` repository secret (it's never committed). When running from source, put your own key in a `.env` file next to `run_rennix.py`:

```
TMDB_READ_TOKEN=your-api-read-access-token
```

or paste it into **Settings → Matching** in the app.

## Project layout

| Path | What it is |
| --- | --- |
| `rennix/parse.py` | Reads titles, years, episodes and quality from file names |
| `rennix/identify.py` | The identification engine: evidence, search tiers, validation, scoring |
| `rennix/tmdb.py` | TheMovieDB client with caching and retries |
| `rennix/naming.py` | Templates, the custom format language and safe file names |
| `rennix/renamer.py` | Planning, the crash journal, renaming and undo |
| `rennix/api.py` | The bridge between the window and the engine |
| `rennix/ui/` | The app's interface (HTML, CSS, JavaScript) |
| `tests/` | 300+ automated tests, using an offline copy of TheMovieDB data |

## Credits

This product uses the TMDB API but is not endorsed or certified by TMDB.
