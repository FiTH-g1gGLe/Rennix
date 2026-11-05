"""Naming formats: templates, custom formats and safe names."""

import pytest

from rennix.naming import (PRESETS, SAMPLE_FACTS, Formats, FormatError, clean_segment, format_to_text,
                           parse_format, parts_from_json, parts_to_json, render, validate)


def ep(**kw):
    return dict(SAMPLE_FACTS["episode"], **kw)


def mv(**kw):
    return dict(SAMPLE_FACTS["movie"], **kw)


@pytest.mark.parametrize("name,expected", [
    ("Plex", "The Matrix (1999) {tmdb-603}/The Matrix (1999).mkv"),
    ("Jellyfin", "The Matrix (1999) [tmdbid-603]/The Matrix (1999).mkv"),
    ("Emby", "The Matrix (1999) [tmdbid=603]/The Matrix (1999).mkv"),
    ("Kodi", "The Matrix (1999)/The Matrix (1999).mkv"),
    ("Simple", "The Matrix (1999).mkv"),
    ("Detailed", "The Matrix (1999) [1080p BluRay].mkv"),
])
def test_movie_templates(name, expected):
    assert render(PRESETS[name]["movie"], mv(), ".mkv") == expected


@pytest.mark.parametrize("name,expected", [
    ("Plex", "Breaking Bad (2008)/Season 02/Breaking Bad (2008) - S02E05 - Breakage.mkv"),
    ("Kodi", "Breaking Bad/Season 2/Breaking Bad S02E05 - Breakage.mkv"),
    ("Simple", "Breaking Bad - 2x05 - Breakage.mkv"),
])
def test_episode_templates(name, expected):
    assert render(PRESETS[name]["episode"], ep(), ".mkv") == expected


def test_every_template_is_valid():
    for name, f in PRESETS.items():
        for kind in ("movie", "episode"):
            assert not [i for i in validate(f[kind], kind) if i["level"] == "error"], (name, kind)


def test_optional_part_disappears_when_empty():
    assert render("{title}[ - {edition}]", mv(edition=""), ".mkv") == "The Matrix.mkv"
    assert render("{title}[ - {edition}]", mv(edition="Extended"), ".mkv") == "The Matrix - Extended.mkv"


def test_nested_optional_parts():
    f = r"{title}[ \[{resolution}[ {hdr}]\]]"
    assert render(f, mv(hdr=""), "") == "The Matrix [1080p]"
    assert render(f, mv(hdr="HDR10"), "") == "The Matrix [1080p HDR10]"
    assert render(f, mv(resolution="", hdr="HDR10"), "") == "The Matrix"


def test_double_episode():
    out = render(PRESETS["Plex"]["episode"], ep(episodes=[1, 2], episode_titles=["Pilot", "Second"]), ".mkv")
    assert out.endswith("S02E01-E02 - Pilot & Second.mkv")


def test_missing_episode_title_leaves_no_dangling_dash():
    out = render(PRESETS["Plex"]["episode"], ep(episode_titles=[]), ".mkv")
    assert out.endswith("Breaking Bad (2008) - S02E05.mkv")


@pytest.mark.parametrize("opts,expected", [("", "S02E05"), ("x", "2x05"), ("short", "S2E5"), ("long", "Season 2 Episode 5")])
def test_episode_styles(opts, expected):
    fmt = "{s00e00" + (":" + opts if opts else "") + "}"
    assert render(fmt, ep(), "") == expected


def test_colons_and_banned_characters():
    assert render("{title}", mv(title="Dune: Part Two"), ".mkv") == "Dune - Part Two.mkv"
    assert render("{title}", mv(title='What If...? <"Pilot">'), "") == "What If... 'Pilot'"
    assert render("{title}", mv(title="Face/Off"), ".mkv") == "Face-Off.mkv"   # a slash in a title never makes a folder


def test_trailing_dots_and_reserved_names():
    assert clean_segment("Movie. ") == "Movie"
    assert clean_segment("CON") == "CON_"


def test_the_option_and_first_letter():
    assert render("{title:the}", mv(), "") == "Matrix, The"
    assert render("{title:first}/{title}", mv(), "") == "M/The Matrix"


def test_zero_padding():
    assert render("{season:00}x{episode:000}", ep(), "") == "02x005"


def test_length_limit_shortens_episode_title_first():
    long_title = "An Extremely Long Episode Title That Keeps Going And Going Far Beyond Reason"
    out = render(PRESETS["Plex"]["episode"], ep(episode_titles=[long_title]), ".mkv", max_path=110, base_len=20)
    assert len(out) + 21 <= 110
    assert "S02E05" in out


def test_roundtrip_text_and_parts():
    for f in PRESETS.values():
        for kind in ("movie", "episode"):
            parts = parse_format(f[kind])
            assert format_to_text(parts) == f[kind]
            assert format_to_text(parts_from_json(parts_to_json(parts))) == f[kind]


@pytest.mark.parametrize("text,kind,needle", [
    ("{title} [ - {edition}", "movie", "never closed"),
    ("{title} ]", "movie", "without a matching"),
    ("{show} - {episode_title}", "episode", "episode number"),
    ("{year}", "movie", "need {title}"),
    ("{title} {bogus}", "movie", "Unknown part"),
])
def test_validation_messages(text, kind, needle):
    msgs = " ".join(i["message"] for i in validate(text, kind))
    assert needle in msgs


def test_saved_formats(tmp_path):
    f = Formats(tmp_path / "formats.json")
    f.save("Mine", movie="{title} [{year}]")
    again = Formats(tmp_path / "formats.json")
    assert again.get("Mine", "movie") == "{title} [{year}]"
    assert again.get("Mine", "episode") == PRESETS["Plex"]["episode"]
    with pytest.raises(FormatError):
        again.save("Plex", movie="{title}")
    again.delete("Mine")
    assert "Mine" not in Formats(tmp_path / "formats.json").all()
