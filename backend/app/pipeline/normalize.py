"""Map raw location / level / skill / work-mode strings (VI or EN) to canonical English keys."""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import cache
from pathlib import Path

import yaml

from app.pipeline.text import fold, slug, word_pattern

DATA_DIR = Path(__file__).parent / "data"


@cache
def _load(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


@cache
def _alias_patterns(name: str, key: str, exclude: frozenset[str] = frozenset()):
    """[(canonical, compiled regex)] with longer aliases first so 'team lead' beats 'lead'."""
    entries = [
        (canonical, alias)
        for canonical, aliases in _load(name)[key].items()
        for alias in aliases
        if alias not in exclude
    ]
    entries.sort(key=lambda e: -len(e[1]))
    return [(canonical, re.compile(word_pattern(alias))) for canonical, alias in entries]


def _find_all(text: str, patterns) -> list[str]:
    """Canonical keys found in text, ordered by first occurrence."""
    hits: dict[str, int] = {}
    for canonical, pattern in patterns:
        match = pattern.search(text)
        if match and (canonical not in hits or match.start() < hits[canonical]):
            hits[canonical] = match.start()
    return sorted(hits, key=hits.get)


def normalize_locations(raw: Iterable[str | None]) -> list[str]:
    patterns = _alias_patterns("locations", "locations")
    found: list[str] = []
    for value in raw:
        if not value:
            continue
        for key in _find_all(fold(value), patterns):
            if key not in found:
                found.append(key)
    return found


LEVEL_ORDER = list(_load("levels")["levels"])


def location_label(key: str) -> str:
    """Display name of a canonical location key, e.g. 'hcmc' -> 'Ho Chi Minh City'."""
    return _load("locations").get("labels", {}).get(key) or key.replace("_", " ").title()


def detect_levels(title: str) -> list[str]:
    """Seniority levels named in a title, ordered junior -> senior.

    "Middle/Senior Java Developer" -> ["mid", "senior"]
    """
    found = _find_all(fold(title), _alias_patterns("levels", "levels"))
    return sorted(found, key=LEVEL_ORDER.index)


def _skill_alias_map() -> dict[str, str]:
    return {
        alias: canonical
        for canonical, aliases in _load("skills")["skills"].items()
        for alias in aliases
    }


def normalize_skill_tags(tags: Iterable[str]) -> list[str]:
    """Structured tags from a source -> canonical keys; unknown tags become slugs."""
    aliases = _skill_alias_map()
    result: list[str] = []
    for tag in tags:
        folded = fold(tag)
        key = aliases.get(folded) or slug(tag)
        if key and key not in result:
            result.append(key)
    return result


def extract_skills(text: str) -> list[str]:
    """Skills mentioned in free text (Facebook posts). Ambiguous aliases are ignored."""
    exclude = frozenset(_load("skills")["tag_only"])
    return _find_all(fold(text), _alias_patterns("skills", "skills", exclude))


WORK_MODES = {
    "remote": ["remote", "lam tu xa", "work from home", "wfh"],
    "hybrid": ["hybrid", "linh hoat", "ket hop"],
    "onsite": [
        "at office",
        "onsite",
        "on-site",
        "in office",
        "tai van phong",
        "lam viec tai van phong",
    ],
}


def normalize_work_mode(raw: str | None) -> str | None:
    if not raw:
        return None
    text = fold(raw)
    for mode, aliases in WORK_MODES.items():
        if any(re.search(word_pattern(a), text) for a in aliases):
            return mode
    return None
