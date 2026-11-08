<div align="center">

<img src="assets/banner.png" alt="Rennix: rename and organise your movies and TV shows" width="100%">

<br>

Drop in a messy folder. Rennix works out what every file is, shows you the new names, and sorts everything in one click.
Nothing changes until you've checked the preview, and every rename can be undone.

<a href="https://github.com/FiTH-g1gGLe/Rennix/releases/latest"><img src="docs/badges/download-windows.svg" alt="Download for Windows" height="52"></a>
&nbsp;
<a href="https://github.com/FiTH-g1gGLe/Rennix/releases/latest"><img src="docs/badges/download-portable.svg" alt="Portable zip" height="52"></a>

[![Build](https://github.com/FiTH-g1gGLe/Rennix/actions/workflows/build.yml/badge.svg)](https://github.com/FiTH-g1gGLe/Rennix/actions/workflows/build.yml)
[![Latest release](https://img.shields.io/github/v/release/FiTH-g1gGLe/Rennix?label=latest&color=2DD4BF)](https://github.com/FiTH-g1gGLe/Rennix/releases)
[![Downloads](https://img.shields.io/github/downloads/FiTH-g1gGLe/Rennix/total?label=downloads&color=2DD4BF&logo=github)](https://github.com/FiTH-g1gGLe/Rennix/releases)
[![Licence: MIT](docs/badges/licence-mit.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Data: TMDB](https://img.shields.io/badge/data-TMDB-01B4E4)](https://www.themoviedb.org/)

<img src="docs/screenshots/preview.png" alt="Rennix showing a preview of new file names, with green, amber and red match status" width="900">

</div>

> [!NOTE]
> Rennix is in **early alpha**. It's being tested on real libraries before the first stable release. Try it on **copies** of your files first.

## ✨ Features

| | |
| --- | --- |
| 🎯 **Smart matching** | Reads messy scene names, folders, IDs and `.nfo` files, then checks every match against [TheMovieDB](https://www.themoviedb.org/) using the year, runtime, episode numbers and episode titles |
| 🚦 **Honest confidence** | Green when it's sure, amber when it wants you to check, red when it can't tell. Hover any row to see why |
| 🛡️ **Never guesses silently** | Remakes like *Dune* (1984 vs 2021) and *The Office* (US vs UK) are caught, not renamed wrongly |
| 👆 **Manual override** | Change any match, search with posters, and Rennix remembers your choice next time |
| 👀 **Quick preview** | Rest the mouse on a file to see its poster, plot, runtime, rating, why it matched and exactly where it will go |
| 🖼️ **Three views** | A detailed list, a poster wall to spot wrong matches at a glance, and a folder view of how your library will look |
| 🏷️ **Naming templates** | Plex, Jellyfin, Emby and Kodi out of the box, or build your own with a live preview |
| 💬 **Brings the extras along** | Subtitles, posters and `.nfo` files are renamed with their video |
| 🔗 **Move, copy or hardlink** | Hardlinks give your library proper names while your downloads keep seeding, using no extra space |
| ↩️ **Undo anything** | Every batch is kept in History and can be reversed in one click |
| 💥 **Crash-safe** | A rename journal means a crash or power cut never leaves files half-renamed |
| 🐞 **Clear error codes** | A built-in console logs everything, and every problem has a code (like `RNX-R001`) with a plain explanation. One click copies a report for support |
| 📺 **TV done properly** | Double episodes, specials, anime numbering and daily shows by air date |
| 📝 **Metadata (optional)** | Posters, logos, season posters, episode thumbnails and `.nfo` files, named for your media server. Every item has its own switch, and it's all off until you turn it on |
| 🏷️ **Tags inside the file (optional)** | Title, plot, cast and a cover written into MKV and MP4 files. Only the header changes; every write is backed up, checked against the original video data and can be undone |

## 📸 Screenshots

<table>
  <tr>
    <td><img src="docs/screenshots/hover.png" alt="Quick preview when hovering a file"></td>
    <td><img src="docs/screenshots/posters.png" alt="Poster wall view"></td>
  </tr>
  <tr>
    <td align="center"><b>👀 Hover any file for a quick preview</b></td>
    <td align="center"><b>🖼️ Poster wall: spot wrong matches at a glance</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/folders.png" alt="Folder view"></td>
    <td><img src="docs/screenshots/metadata.png" alt="Metadata settings"></td>
  </tr>
  <tr>
    <td align="center"><b>📁 See your library before anything moves</b></td>
    <td align="center"><b>📝 Choose exactly which metadata to save</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/match-panel.png" alt="Fixing an unsure match"></td>
    <td><img src="docs/screenshots/format-editor.png" alt="Naming format editor"></td>
  </tr>
  <tr>
    <td align="center"><b>👆 Pick the right match when Rennix isn't sure</b></td>
    <td align="center"><b>🏷️ Build your own naming format</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/console.png" alt="Console with error codes"></td>
    <td><img src="docs/screenshots/start.png" alt="First launch"></td>
  </tr>
  <tr>
    <td align="center"><b>🐞 Console with clear error codes</b></td>
    <td align="center"><b>📂 Drop a folder to start</b></td>
  </tr>
</table>

## 📝 Metadata

Turn on **📝 Metadata** in the bottom bar (or **Settings → Metadata**) and Rennix saves details alongside every rename. Files that are already named correctly get them too, without being renamed. Every item has its own switch:

| | Item | Plex | Jellyfin · Emby · Kodi |
| --- | --- | :---: | :---: |
| Next to the video | 🖼️ Poster | ✅ | ✅ |
| | 🏷️ Logo | ✅ | ✅ |
| | 📝 `.nfo` info file (plot, cast, director, ratings, IDs) | ❌ ignored by Plex | ✅ |
| | 📺 Season posters | ✅ | ✅ |
| | 🎞️ Episode thumbnails | ✅ | ✅ |
| Inside the file | 🏷️ Tags (title, year, plot, genres, director, cast, age rating) | MP4 only | ✅ |
| | 🖼️ Embedded cover | | ✅ |

Artwork is named the way each media server expects. Existing posters and `.nfo` files are kept unless you turn on **Replace existing**, and Undo removes everything Rennix saved.

> [!TIP]
> **Plex users:** turn on **Use local assets** in your library's settings so Plex picks up the posters and logos.

> [!NOTE]
> Windows Explorer's **Properties → Details** only shows the title for MKV files. The other tags are there (VLC, MediaInfo and media servers read them); free add-ons such as Icaros can show more in Explorer.

## 📥 Download

| Version | Best for |
| --- | --- |
| [**Rennix Setup** (.exe)](https://github.com/FiTH-g1gGLe/Rennix/releases/latest) | Most people. Installs for your user only, no admin rights needed |
| [**Portable** (.zip)](https://github.com/FiTH-g1gGLe/Rennix/releases/latest) | Running from a USB stick or without installing. Keeps its settings beside the app |

> [!TIP]
> Windows may show **"Windows protected your PC"** for new apps. Click **More info → Run anyway**.

Mac and Linux builds are planned. Until then, run from source (below); the code is fully cross-platform.

## 🚀 How it works

1. **Drop** files or folders onto the window
2. **Check** the preview: green rows are ready, amber rows need a quick look, red rows need a manual match
3. **Rename** with one click, and undo from History if you change your mind

### Example names

| Before | After (Plex template) |
| --- | --- |
| `The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv` | `The Matrix (1999) {tmdb-603}/The Matrix (1999).mkv` |
| `breaking bad s02e05 720p.mkv` | `Breaking Bad (2008)/Season 02/Breaking Bad (2008) - S02E05 - Breakage.mkv` |
| `the.office.us.s03e01e02.mkv` | `The Office (2005)/Season 03/The Office (2005) - S03E01-E02 - Gay Witch Hunt & The Convention.mkv` |
| `Dune.2021.2160p.UHD.BluRay.x265.mkv` | `Dune (2021) {tmdb-438631}/Dune (2021).mkv` |

## 🗺️ Roadmap

- [x] Identification engine, naming templates and custom formats
- [x] Safe renaming with crash journal and undo
- [x] Windows installer, portable version and auto-update check
- [x] Quick preview, poster wall and folder views
- [x] Console with error codes and one-click support reports
- [x] **Version 2:** posters, logos, `.nfo` files and tags inside MKV and MP4 files
- [ ] Real-world testing and fixes *(in progress)*
- [ ] Artwork picker and a per-file metadata preview
- [ ] File fingerprinting for badly named files
- [ ] Library health check, watch folders, Plex and Jellyfin refresh
- [ ] Mac and Linux builds

## ❓ FAQ

<details>
<summary><b>Where does Rennix keep its data?</b></summary>

Settings and your custom formats are in `%APPDATA%\Rennix`; the match cache, undo history and logs are in `%LOCALAPPDATA%\Rennix`. The portable version keeps everything in a `data` folder beside the app. Nothing is ever stored in Temp.
</details>

<details>
<summary><b>Does it change the video files themselves?</b></summary>

Not unless you ask it to. Renaming only changes names and locations; moves between drives are copied, size-checked, and only then is the original removed.

Writing **tags inside the file** is optional and off by default. When it's on, Rennix uses [mkvpropedit](https://mkvtoolnix.download/) for MKV and [mutagen](https://mutagen.readthedocs.io/) for MP4, which change only the file's header, never the video or audio. Before each write Rennix backs up the exact bytes that may change and fingerprints the video data; afterwards it checks the video data is byte-for-byte identical and the tags read back. If anything is off, the file is put back exactly as it was. **Maximum safety** in Settings writes to a copy first and swaps it in only once it's checked.
</details>

<details>
<summary><b>Do I need a TheMovieDB account?</b></summary>

No. Rennix has its own key built in. You can add your own in **Settings → Matching** if you ever want to.
</details>

<details>
<summary><b>Does Rennix send any data about me?</b></summary>

No. It only looks up titles on TheMovieDB and checks GitHub for updates. Nothing about you or your files is collected.
</details>

## 🛠️ For developers: running from source

> You **don't** need any of this to use Rennix. Just [download it](https://github.com/FiTH-g1gGLe/Rennix/releases/latest) and double-click.

This section is for people who want to change the code. You need Python 3.11 or newer.

```bash
git clone https://github.com/FiTH-g1gGLe/Rennix.git
cd Rennix
pip install -r requirements.txt
python run_rennix.py
```

Run the tests with `python -m pytest`. Every push is tested on Windows, Mac and Linux, and Windows builds are produced automatically by [GitHub Actions](https://github.com/FiTH-g1gGLe/Rennix/actions).

<details>
<summary><b>Project layout</b></summary>

| Path | What it is |
| --- | --- |
| `rennix/parse.py` | Reads titles, years, episodes and quality from file names |
| `rennix/identify.py` | The identification engine: evidence, search tiers, validation and scoring |
| `rennix/tmdb.py` | TheMovieDB client with caching and retries |
| `rennix/naming.py` | Templates, the custom format language and safe file names |
| `rennix/renamer.py` | Planning, the crash journal, renaming and undo |
| `rennix/metadata.py` | Version 2: details, artwork, `.nfo` files and where they go for each media server |
| `rennix/embed.py` | Safe tag writing inside MKV and MP4 files, with backups, checks and undo |
| `rennix/ebml.py`, `rennix/mp4box.py` | Read-only MKV and MP4 structure readers used for those checks |
| `rennix/api.py` | The bridge between the window and the engine |
| `rennix/ui/` | The interface (HTML, CSS and JavaScript) |
| `tests/` | 360+ automated tests, including real MKV and MP4 files and "pull the plug" crash tests |
</details>

## 🙏 Credits

This product uses the TMDB API but is not endorsed or certified by TMDB.

The Windows version includes `mkvpropedit` from [MKVToolNix](https://mkvtoolnix.download/) by Moritz Bunkus (GNU GPL v2) for writing MKV tags.

## 📄 Licence

Rennix is released under the [MIT Licence](LICENSE).
