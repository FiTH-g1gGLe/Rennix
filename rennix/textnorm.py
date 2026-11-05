"""Title normalisation and fuzzy comparison.

``Spiderman`` = ``Spider-Man``, ``Amelie`` = ``Amélie``, ``Rocky II`` = ``Rocky 2``,
``Alien³`` = ``Alien 3``, ``&`` = ``and``.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

try:
    from rapidfuzz import fuzz as _fuzz
except ImportError:  # fallback keeps everything working without rapidfuzz
    _fuzz = None

_ROMAN = {
    "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6",
    "vii": "7", "viii": "8", "ix": "9", "x": "10", "xi": "11", "xii": "12",
}
_SUPERSCRIPTS = str.maketrans({"²": " 2", "³": " 3", "¹": " 1"})
_APOSTROPHES = re.compile(r"['’`´]")
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_LEADING_ARTICLE = re.compile(r"^(the|a|an) ")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str, drop_article: bool = True) -> str:
    """Lower-case, accent-free, punctuation-free form used for comparing titles."""
    if not text:
        return ""
    s = text.translate(_SUPERSCRIPTS)
    s = strip_accents(s).lower()
    s = s.replace("&", " and ").replace("+", " plus ")
    s = _APOSTROPHES.sub("", s)
    s = _NON_WORD.sub(" ", s).replace("_", " ")
    tokens = [_ROMAN.get(t, t) for t in s.split()]
    s = " ".join(tokens)
    if drop_article:
        s = _LEADING_ARTICLE.sub("", s)
    return s.strip()


def _ratio(a: str, b: str) -> float:
    if _fuzz is not None:
        return _fuzz.token_sort_ratio(a, b) / 100.0
    a_sorted = " ".join(sorted(a.split()))
    b_sorted = " ".join(sorted(b.split()))
    return difflib.SequenceMatcher(None, a_sorted, b_sorted).ratio()


def similarity(a: str, b: str) -> float:
    """0.0 to 1.0 similarity between two titles after normalising both."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na.replace(" ", "") == nb.replace(" ", ""):
        return 1.0  # Spiderman vs Spider-Man
    return max(_ratio(na, nb), _ratio(na.replace(" ", ""), nb.replace(" ", "")) * 0.98)


def best_similarity(title: str, candidates: list[str]) -> float:
    return max((similarity(title, c) for c in candidates if c), default=0.0)
