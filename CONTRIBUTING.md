# Contributing to Rennix

Thanks for wanting to help! Bug reports, ideas and code are all welcome.

## Reporting a bug

Open a [bug report](https://github.com/FiTH-g1gGLe/Rennix/issues/new?template=bug_report.yml) and paste the **diagnostic report** from the 🐞 console (**Ctrl + Shift + L → Copy report**). It contains the error codes and recent log, which is usually all that's needed to find the problem.

## Suggesting a feature

Open a [feature request](https://github.com/FiTH-g1gGLe/Rennix/issues/new?template=feature_request.yml) and describe what you're trying to do. The "why" helps more than the "how".

## Working on the code

You need Python 3.11 or newer.

```bash
git clone https://github.com/FiTH-g1gGLe/Rennix.git
cd Rennix
pip install -r requirements.txt
python run_rennix.py        # run the app
python -m pytest            # run the tests
```

The tests use an offline copy of TheMovieDB data, so they don't need a key or an internet connection. The metadata tests that use real MKV and MP4 files run when `ffmpeg`, `mkvpropedit` and `mutagen` are installed, and are skipped otherwise.

### Ground rules

- **Never risk a user's files.** Renames go through the plan → journal → execute → undo path in `renamer.py`. Anything that changes a file's contents goes through `embed.py`, with a backup and before/after checks.
- **Every problem gets a code.** Add new error codes to `rennix/diagnostics.py` with a plain-English explanation and what to do.
- **Friendly words.** Messages and tooltips are short, plain English, with no jargon.
- **Tests for everything.** A bug fix comes with a test that would have caught it.

Every push is tested on Windows, Mac and Linux by GitHub Actions, which also builds the Windows installer and portable version.
