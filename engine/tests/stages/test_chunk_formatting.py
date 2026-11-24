"""Inline formatting through a chunk (CGL-108): markers reach the translators and the reviewer,
come back as marks on the passage, and a broken answer leaves the passage plain and flagged."""

import json

from coglossa.llm.types import ContextOverflowError
from coglossa.schema import (
    Block,
    BlockKind,
    Chunk,
    FlagCode,
    InlineMark,
    MarkKind,
    Provenance,
    Reliability,
    SourceDocument,
)
from coglossa.segment.segmenter import segment_document
from coglossa.stages.chunk import process_chunk
from coglossa.stages.context import StageContext
from coglossa.stages.translate import build_chunk_inputs
from coglossa.testing.fake_scorer import FakeScorer
from tests.conftest import BASE_CONFIG
from tests.stages.test_chunk import GUIDE, PROFILE

TEXT = "Le chat dort. Il pleut ici."


def _ci():
    marks = [
        InlineMark(start=3, end=7, kind=MarkKind.BOLD),
        InlineMark(start=23, end=26, kind=MarkKind.LINK, href="https://example.org/"),
    ]
    block = Block(
        id="b1",
        kind=BlockKind.PARAGRAPH,
        text=TEXT,
        provenance=Provenance(source_path="x.docx", index=0, marks=marks),
    )
    doc = SourceDocument(source_path="x.docx", format="docx", sha256="0", blocks=[block])
    segments = segment_document(doc)
    chunk = Chunk(id="c1", segment_ids=[s.id for s in segments])
    return build_chunk_inputs([chunk], segments, doc.blocks)[0]


def _payloads(fake_backend, task):
    out = []
    for r in fake_backend.requests:
        payload = json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])
        if payload["task"] == task:
            out.append(payload)
    return out


async def _run(make_runner, ci, cfg=None, scorer=None):
    runner = make_runner(cfg)
    ctx = StageContext(cfg=runner.cfg, runner=runner, target_lang="en")
    return await process_chunk(
        ctx, scorer, PROFILE, GUIDE, ci, tier=Reliability.A, diverse=False, on_stage=None
    )


async def test_markers_go_out_and_come_back_as_the_passages_formatting(make_runner, fake_backend):
    out = await _run(make_runner, _ci())
    for payload in _payloads(fake_backend, "translate"):
        assert [s["text"] for s in payload["segments"]] == [
            "Le <1>chat</1> dort.",
            "Il pleut <2>ici</2>.",
        ]
    reviewed = _payloads(fake_backend, "reconcile")[0]
    assert reviewed["segments"][0]["text"] == "Le <1>chat</1> dort."
    assert reviewed["candidates"][0]["spans"][0]["text"] == "[en] Le <1>chat</1> dort."
    # nothing stored or shown carries a marker
    assert [s.text for s in out.spans] == ["[en] Le chat dort.", "[en] Il pleut ici."]
    assert all("<" not in c.text for c in out.candidates)
    first, second = out.spans
    assert first.flags == [] and second.flags == []
    assert first.formatting is not None and first.formatting.text == first.text
    assert [(m.kind, first.text[m.start : m.end]) for m in first.formatting.marks] == [
        (MarkKind.BOLD, "chat")
    ]
    assert [(m.href, second.text[m.start : m.end]) for m in second.formatting.marks] == [
        ("https://example.org/", "ici")
    ]


async def test_a_broken_answer_gives_the_plain_passage_and_a_flag(make_runner, fake_backend):
    review = {
        "spans": [
            {
                "start": start,
                "end": start,
                "text": text,
                "agreement": "agree",
                "chosen_from": "literal",
            }
            for start, text in (("s1", "The <1>cat sleeps."), ("s2", "It rains <2>here</2>."))
        ]
    }
    fake_backend.script("reconcile", json.dumps(review))
    out = await _run(make_runner, _ci())
    first, second = out.spans
    assert first.text == "The cat sleeps." and first.formatting is None
    assert [f.code for f in first.flags] == [FlagCode.FORMATTING_LOST]
    assert first.flags[0].explanation.startswith("Formatting could not be kept here")
    assert second.formatting is not None and second.flags == []
    assert out.errors == [] and not out.engine_error


async def test_scoring_reads_plain_text(make_runner, fake_backend, make_config):
    cfg = make_config(
        models={**BASE_CONFIG["models"], "model-c": {"backend": "fake", "context_window": 32768}},
        roles={
            **BASE_CONFIG["roles"],
            "judge": {"model": "model-c"},
            "roundtrip": {"model": "model-c"},
        },
    )
    scorer = FakeScorer()
    seen = []
    original = scorer.qe

    async def qe(items):
        seen.extend(items)
        return await original(items)

    scorer.qe = qe
    out = await _run(make_runner, _ci(), cfg, scorer)
    assert seen and all("<" not in i.source + i.hypothesis for i in seen)
    for task in ("judge", "back_translate"):
        payload = json.dumps(_payloads(fake_backend, task), ensure_ascii=False)
        assert "<1>" not in payload and "</2>" not in payload
    assert out.spans[0].formatting is not None


async def test_a_split_chunk_keeps_its_markers(make_runner, fake_backend):
    fake_backend.script("translate", ContextOverflowError("maximum context length exceeded"))
    out = await _run(make_runner, _ci())
    assert [s.text for s in out.spans] == ["[en] Le chat dort.", "[en] Il pleut ici."]
    assert all(s.formatting is not None for s in out.spans)
