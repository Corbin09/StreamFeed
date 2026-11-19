import json

import httpx

from app.config import Settings
from app.pipeline.classify import HiringFilter
from app.pipeline.llm import EXAMPLES, MAX_CHARS, LlmClassifier


def ollama(reply: dict | str, status: int = 200, seen: list | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return httpx.Response(status, json={"message": {"role": "assistant", "content": content}})

    return httpx.MockTransport(handler)


def test_classifier_sends_schema_examples_and_truncated_post() -> None:
    seen: list = []
    llm = LlmClassifier("http://ollama:11434/", "m", transport=ollama({"hiring": True}, seen=seen))
    assert llm.classify("tuyn dev " + "x" * 2000) is True

    body = seen[0]
    assert body["model"] == "m"
    assert body["format"]["required"] == ["hiring"]
    assert body["options"]["temperature"] == 0
    messages = body["messages"]
    assert messages[0]["role"] == "system"
    assert len(messages) == 1 + 2 * len(EXAMPLES) + 1
    assert len(messages[-1]["content"]) == len("Post:\n") + MAX_CHARS


def test_classifier_returns_false() -> None:
    llm = LlmClassifier("http://x", "m", transport=ollama({"hiring": False}))
    assert llm.classify("em đang tìm việc") is False


def test_failure_disables_the_llm_for_the_batch() -> None:
    calls: list = []
    llm = LlmClassifier("http://x", "m", transport=ollama({"error": "x"}, status=404, seen=calls))
    assert llm.classify("a") is None
    assert llm.classify("b") is None
    assert len(calls) == 1  # no second request after the first failure
    assert llm.available is False


def test_malformed_reply_is_a_failure() -> None:
    llm = LlmClassifier("http://x", "m", transport=ollama("not json"))
    assert llm.classify("a") is None


def test_filter_uses_llm_then_falls_back_to_contact_rules() -> None:
    # The LLM rejects a post the contact rules would keep.
    keep_by_rules = "Tuyển dev PHP, lh 0912345678"
    hiring = HiringFilter(LlmClassifier("http://x", "m", transport=ollama({"hiring": False})))
    assert hiring(keep_by_rules) is False
    assert (hiring.by_llm, hiring.by_rules) == (1, 0)

    down = HiringFilter(LlmClassifier("http://x", "m", transport=ollama("", status=500)))
    assert down(keep_by_rules) is True
    assert down("Bán bàn phím cơ cũ") is False
    assert (down.by_llm, down.by_rules) == (0, 2)


def test_filter_without_llm_url_uses_rules_only() -> None:
    hiring = HiringFilter.from_settings(Settings(llm_url=""))
    assert hiring.llm is None
    assert hiring("Tuyển dev, lh 0912345678") is True
