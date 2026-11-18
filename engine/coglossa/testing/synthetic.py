"""Deterministic stand-in for a model: valid output for every agent task, no intelligence.
Translations are "[<target_lang>] <source>" so tests can see exactly what flowed where.
A translation containing MISTRANSLATED gets a critical judge error, so demos and browser tests
have a flag to resolve."""

from collections.abc import Callable
from typing import Any

Payload = dict[str, Any]


def _profile(p: Payload) -> Payload:
    return {
        "source_lang": p.get("source_lang_hint") or "und",
        "mixed_language": False,
        "genre": "other",
        "tone_register": "neutral",
    }


def _guide(p: Payload) -> Payload:
    words = len(str(p.get("text", "")).split())
    return {
        "summary": f"Synthetic summary of {words} words.",
        "style_guide": "Synthetic style guide.",
        "glossary": [],
        "entities": [],
        "cultural_references": [],
    }


def _guide_merge(p: Payload) -> Payload:
    partials = p["partials"]
    return {
        "summary": " ".join(b["summary"] for b in partials),
        "style_guide": partials[0]["style_guide"],
        "glossary": [g for b in partials for g in b.get("glossary", [])],
        "entities": [e for b in partials for e in b.get("entities", [])],
        "cultural_references": [c for b in partials for c in b.get("cultural_references", [])],
    }


def _translate(p: Payload) -> Payload:
    tag = p.get("target_lang", "xx")
    return {
        "spans": [
            {"start": s["id"], "end": s["id"], "text": f"[{tag}] {s['text']}", "notes": ""}
            for s in p["segments"]
        ]
    }


MISTRANSLATED = "MISTRANSLATED"  # a span whose translation contains it gets a critical error


def _judge(p: Payload) -> Payload:
    def errors(text: str) -> list[Payload]:
        if MISTRANSLATED not in text:
            return []
        return [
            {
                "category": "accuracy/mistranslation",
                "severity": "critical",
                "excerpt": MISTRANSLATED,
                "explanation": f"Synthetic critical error: the text is marked {MISTRANSLATED}.",
            }
        ]

    return {
        "spans": [
            {"start": s["start"], "end": s["end"], "errors": errors(s["text"])} for s in p["spans"]
        ]
    }


def _back_translate(p: Payload) -> Payload:
    tag = p.get("to_lang", "xx")
    return {
        "spans": [
            {"start": s["start"], "end": s["end"], "text": f"[{tag}] {s['text']}", "notes": ""}
            for s in p["spans"]
        ]
    }


def _reconcile(p: Payload) -> Payload:
    first = p["candidates"][0]
    return {
        "spans": [
            {
                "start": s["start"],
                "end": s["end"],
                "text": s["text"],
                "agreement": "agree",
                "chosen_from": first["translator"],
                "rationale": "",
                "issues": [],
                "cultural_or_wordplay": False,
                "proposed_terms": [],
            }
            for s in first["spans"]
        ]
    }


def _sweep(p: Payload) -> Payload:
    text = p["translation"]
    missing = [
        r["rendering"]
        for r in p["requirements"]
        if r["rendering"].casefold() not in text.casefold()
    ]
    return {"text": " ".join([text, *missing])}


def _inject(p: Payload) -> Payload:
    prefix = "NOT " if p.get("kind") == "negation" else "YO, "
    return {"text": prefix + str(p["text"])}


def _ok(p: Payload) -> Payload:
    return {"ok": True}


_HANDLERS: dict[str, Callable[[Payload], Payload]] = {
    "profile": _profile,
    "guide": _guide,
    "guide_merge": _guide_merge,
    "translate": _translate,
    "reconcile": _reconcile,
    "sweep": _sweep,
    "inject": _inject,
    "judge": _judge,
    "back_translate": _back_translate,
    "ping": _ok,
    "probe": _ok,
}


def synthetic_response(payload: Payload) -> Payload:
    handler = _HANDLERS.get(payload.get("task", ""))
    if handler is None:
        raise ValueError(f"unknown task '{payload.get('task')}'")
    return handler(payload)


def chat_completion(content: str, *, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10},
    }
