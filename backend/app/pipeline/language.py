"""Detect whether post text is Vietnamese or English."""

from __future__ import annotations

from functools import cache

from lingua import Language, LanguageDetector, LanguageDetectorBuilder

MIN_LENGTH = 20
SAMPLE_LENGTH = 2000
_CODES = {Language.VIETNAMESE: "vi", Language.ENGLISH: "en"}


@cache
def _detector() -> LanguageDetector:
    return LanguageDetectorBuilder.from_languages(*_CODES).build()


def detect_language(text: str) -> str | None:
    """'vi', 'en', or None when the text is too short to tell."""
    sample = " ".join(text.split())[:SAMPLE_LENGTH]
    if len(sample) < MIN_LENGTH:
        return None
    language = _detector().detect_language_of(sample)
    return _CODES.get(language) if language else None
