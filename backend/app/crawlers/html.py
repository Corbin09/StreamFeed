"""HTML helpers for parsers."""

from __future__ import annotations

import html as html_lib
import json
import re
from collections.abc import Iterator
from typing import Any

from selectolax.lexbor import LexborHTMLParser

_BLOCK_TAGS = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|section)\b[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]+>")
_BLANK_LINES = re.compile(r"\n\s*\n+")


def html_to_text(fragment: str) -> str:
    """Convert an HTML fragment to plain text, keeping paragraph and list breaks."""
    text = _BLOCK_TAGS.sub("\n", fragment)
    text = html_lib.unescape(_TAGS.sub("", text)).replace("\r", "").replace("\xa0", " ")
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def json_ld(tree: LexborHTMLParser) -> Iterator[dict[str, Any]]:
    """Yield every JSON-LD object on a page (flattening top-level lists)."""
    for script in tree.css("script[type='application/ld+json']"):
        try:
            data = json.loads(script.text())
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict):
                yield item
