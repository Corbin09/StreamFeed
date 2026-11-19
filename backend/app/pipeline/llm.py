"""Hiring-post classifier backed by a local LLM served by Ollama.

Facebook group posts are often written without diacritics, with typos or with chat
shorthand ("tuyn dev, lh e, ib"), which keyword and contact rules miss. A small
Vietnamese model reads the whole post instead. The default model, Arcee-VyLinh 3B
(Qwen2.5-based, Vietnamese fine-tune), scored best of six local models in a test on real
group posts plus accent-stripped, misspelled and teencode variants: 52/54 correct at about
5 s per post on a 4-core CPU with the examples below (ARCHITECTURE.md §7).

The model answers only `{"hiring": true|false}`: Ollama's `format` JSON schema constrains
the output, and the post is cut to MAX_CHARS, because reading the prompt is most of the
cost on a CPU. A few labelled examples in the prompt teach the hard cases (job seekers,
course ads). The fixed part of the prompt is cached by Ollama between posts.

`classify` returns None when the model cannot answer (server down, model not pulled,
timeout, malformed reply); the caller then falls back to the contact rules.
"""

from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger(__name__)

MAX_CHARS = 800

SYSTEM_PROMPT = (
    "You classify posts from a Vietnamese IT jobs Facebook group. "
    "Posts may be Vietnamese or English, may have no diacritics, typos, slang or teencode "
    "(e.g. 'tuyen', 'tuyn dung', 'lh', 'ib', 'inbox', 'zalo', 'sdt', 'yoe', 'kn'). "
    "Answer hiring=true only if the author is an employer or recruiter offering a job "
    "(any role) to readers. Answer hiring=false for job seekers looking for work, "
    "course or training ads, product ads, questions, opinions and chit-chat. "
    "Reply with JSON only."
)

# (post, hiring) pairs shown to the model before the real post. Written for the prompt;
# not taken from real group members.
EXAMPLES: list[tuple[str, bool]] = [
    (
        "Ben minh can tuyen 1 ban Flutter dev 2 nam kn, lam tai Thanh Xuan, luong 18-22tr. lh zalo",
        True,
    ),
    (
        "Em tốt nghiệp CNTT, 2 năm kinh nghiệm NodeJS, đang tìm việc ở HCM, "
        "anh chị nào cần cho em xin cơ hội",
        False,
    ),
    (
        "Khoa hoc lap trinh Python cap toc 3 thang, cam ket co viec, hoc phi 5tr, inbox de dang ky",
        False,
    ),
    ("[HN] Hiring Senior DevOps, 4+ yoe AWS/K8s, up to 2500$, hybrid. Send CV via ib", True),
    ("Mn cho e hoi phong van fresher java thuong hoi gi a", False),
]

_SCHEMA = {
    "type": "object",
    "properties": {"hiring": {"type": "boolean"}},
    "required": ["hiring"],
}


def _messages(text: str) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for example, hiring in EXAMPLES:
        messages.append({"role": "user", "content": f"Post:\n{example}"})
        messages.append({"role": "assistant", "content": json.dumps({"hiring": hiring})})
    messages.append({"role": "user", "content": f"Post:\n{text[:MAX_CHARS]}"})
    return messages


class LlmClassifier:
    """Calls Ollama's /api/chat. After the first failure it stops calling the server for
    the rest of its life (one crawl batch), so a dead server costs one timeout, not one per
    post."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout: float = 120,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.available = True
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    def classify(self, text: str) -> bool | None:
        if not self.available:
            return None
        body = {
            "model": self.model,
            "stream": False,
            "format": _SCHEMA,
            "options": {"temperature": 0, "num_ctx": 2048},
            "messages": _messages(text),
        }
        try:
            response = self._client.post("/api/chat", json=body)
            response.raise_for_status()
            answer = json.loads(response.json()["message"]["content"])
            return bool(answer["hiring"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            self.available = False
            logger.warning(
                "LLM classifier unavailable (%s: %s); using contact rules for this batch",
                type(exc).__name__,
                exc,
            )
            return None

    def close(self) -> None:
        self._client.close()
