"""Text helpers shared by the pipeline."""

from __future__ import annotations

import re
import unicodedata

_SPACES = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def fold(text: str) -> str:
    """Lower-case and strip Vietnamese diacritics ("Đà Nẵng" -> "da nang").

    Mirrors Postgres `f_unaccent(lower(...))` closely enough for matching.
    """
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _SPACES.sub(" ", stripped.lower()).strip()


def slug(text: str) -> str:
    """Folded text reduced to [a-z0-9-] ("Business Analysis" -> "business-analysis")."""
    return _NON_ALNUM.sub("-", fold(text)).strip("-")


def word_pattern(alias: str) -> str:
    """Regex for an alias bounded by non-word characters. Handles c++, c#, .net."""
    return rf"(?<![a-z0-9+#]){re.escape(alias)}(?![a-z0-9+#])"
