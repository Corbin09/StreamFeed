"""Decide whether a post from a mixed source (e.g. a Facebook group) is a hiring post."""

from __future__ import annotations

import re

from app.pipeline.text import fold, word_pattern

HIRING_KEYWORDS = [
    "tuyen",
    "tuyen dung",
    "can tuyen",
    "dang tuyen",
    "ung tuyen",
    "gui cv",
    "nop cv",
    "hiring",
    "we are hiring",
    "we're hiring",
    "looking for",
    "job description",
    "jd",
    "apply",
    "vacancy",
    "open position",
    "join our team",
]
_HIRING = re.compile("|".join(word_pattern(k) for k in HIRING_KEYWORDS))


def is_hiring_post(text: str) -> bool:
    return bool(_HIRING.search(fold(text)))
