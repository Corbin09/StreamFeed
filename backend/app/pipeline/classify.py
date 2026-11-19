"""Decide whether a post from a mixed source (a Facebook group) is a hiring post.

`HiringFilter` asks the local LLM first (`app.pipeline.llm`, enabled by `LLM_URL`) and
falls back to the contact rules below when the LLM is off or cannot answer.

Contact rules: a hiring post tells people how to reach the employer. Instead of looking for
words such as "tuyển" (which also appear in "trực tuyến", job seekers' posts and course
ads), the post is kept when it carries contact information:

- one **strong** signal: a phone number, an email address, or an application link
  (Google Forms, shortened links, career pages, any http link); or
- **weak** signals from at least two different groups: contact words, messaging channels,
  how to apply, and where the job is.

Matching runs on folded text (lower case, no diacritics), so "Liên hệ", "LIÊN HỆ" and
"lien he" are the same. `contact_signals` returns what was found, for previews and tests.
"""

from __future__ import annotations

import re

from app.config import Settings, get_settings
from app.pipeline.llm import LlmClassifier
from app.pipeline.text import fold, word_pattern

# --- strong signals (raw text, before folding) ---------------------------------------------

# Vietnamese mobile and landline numbers: 0xx / +84 / 84, with optional spaces, dots, dashes.
_PHONE = re.compile(r"(?<![\d.])(?:\+?84|0)[\s.\-]?[1-9](?:[\s.\-]?\d){7,9}(?![\d])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LINK = re.compile(
    r"(https?://\S+|forms\.gle/\S+|docs\.google\.com/forms\S*|bit\.ly/\S+|tinyurl\.com/\S+"
    r"|\b[\w-]+\.(?:com|vn|io|net|com\.vn)/[\w/-]*(?:career|tuyen-?dung|jobs?|apply|recruit)\S*)",
    re.IGNORECASE,
)

# --- weak signals (folded text) ------------------------------------------------------------

WEAK_SIGNALS: dict[str, list[str]] = {
    # Words that introduce contact details.
    "contact": [
        "lien he",
        "lh",
        "contact",
        "hotline",
        "sdt",
        "so dien thoai",
        "dien thoai",
        "phone",
        "tel",
        "goi ngay",
        "call",
        "lien lac",
        "thong tin lien he",
        "hr",
        "nhan su",
    ],
    # Messaging channels.
    "channel": [
        "zalo",
        "zl",
        "telegram",
        "tele",
        "skype",
        "whatsapp",
        "viber",
        "messenger",
        "inbox",
        "inb",
        "ib",
        "nhan tin",
        "dm",
        "email",
        "e-mail",
        "mail",
        "gmail",
    ],
    # How to apply.
    "apply": [
        "gui cv",
        "nop cv",
        "send cv",
        "cv ve",
        "cv qua",
        "cv toi",
        "gui ho so",
        "nop ho so",
        "ung tuyen qua",
        "ung tuyen tai",
        "link ung tuyen",
        "apply qua",
        "apply via",
        "apply at",
        "apply here",
        "send your cv",
        "send resume",
        "comment",
        "de lai sdt",
    ],
    # Where the job is.
    "place": [
        "dia chi",
        "dia diem",
        "noi lam viec",
        "lam viec tai",
        "van phong",
        "tru so",
        "toa nha",
        "address",
        "office",
        "location",
        "work location",
    ],
}

_WEAK = {
    group: re.compile("|".join(word_pattern(w) for w in words))
    for group, words in WEAK_SIGNALS.items()
}
MIN_WEAK_GROUPS = 2


def contact_signals(text: str) -> dict[str, list[str]]:
    """Contact information found in a post, by signal group."""
    found: dict[str, list[str]] = {}
    for name, pattern in (("phone", _PHONE), ("email", _EMAIL), ("link", _LINK)):
        hits = [m.group(0) for m in pattern.finditer(text)]
        if hits:
            found[name] = hits
    folded = fold(text)
    for group, pattern in _WEAK.items():
        hits = sorted({m.group(0) for m in pattern.finditer(folded)})
        if hits:
            found[group] = hits
    return found


STRONG_SIGNALS = ("phone", "email", "link")


def is_hiring_post(text: str) -> bool:
    signals = contact_signals(text)
    if any(name in signals for name in STRONG_SIGNALS):
        return True
    return sum(1 for group in WEAK_SIGNALS if group in signals) >= MIN_WEAK_GROUPS


class HiringFilter:
    """Callable `text -> bool` used by the pipeline; counts which method decided."""

    def __init__(self, llm: LlmClassifier | None = None) -> None:
        self.llm = llm
        self.by_llm = 0
        self.by_rules = 0

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> HiringFilter:
        settings = settings or get_settings()
        if not settings.llm_url:
            return cls()
        llm = LlmClassifier(
            settings.llm_url, settings.llm_model, timeout=settings.llm_timeout_seconds
        )
        return cls(llm)

    def __call__(self, text: str) -> bool:
        if self.llm is not None:
            verdict = self.llm.classify(text)
            if verdict is not None:
                self.by_llm += 1
                return verdict
        self.by_rules += 1
        return is_hiring_post(text)

    def close(self) -> None:
        if self.llm is not None:
            self.llm.close()
