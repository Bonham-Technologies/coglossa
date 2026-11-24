"""What every export shows (spec §7.5): the spans with their source text in reading order,
and the translated text of each source block. Exports read the Report, whose span text is
always the latest revision, so they reflect every sweep and reviewer change."""

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from coglossa.schema import (
    Block,
    BlockKind,
    Flag,
    FlagStatus,
    InlineMark,
    Report,
    Span,
    kept_formatting,
)
from coglossa.segment.markup import join_marked
from coglossa.stages.sweep import span_sources

BREAK_MARKER = "* * *"
_RTL_LANGUAGES = frozenset(
    {"ar", "arc", "ckb", "dv", "fa", "he", "iw", "ps", "sd", "ug", "ur", "yi"}
)
_RTL_SCRIPTS = frozenset({"arab", "hebr", "syrc", "thaa", "nkoo", "adlm", "rohg"})


@dataclass(frozen=True)
class ExportRow:
    number: int  # 1-based reading-order position (the report's p<number>)
    source: str
    span: Span

    @property
    def open_flags(self) -> list[Flag]:
        return [f for f in self.span.flags if f.status is FlagStatus.OPEN]


def export_rows(report: Report) -> list[ExportRow]:
    sources = span_sources(report.spans, report.segments)
    return [
        ExportRow(number=n, source=source, span=span)
        for n, (source, span) in enumerate(zip(sources, report.spans, strict=True), start=1)
    ]


@dataclass(frozen=True)
class TranslatedBlock:
    block: Block
    text: str
    marks: list[InlineMark] = field(default_factory=list)  # the passages' formatting (CGL-108)


def translated_blocks(report: Report) -> list[TranslatedBlock]:
    """Each source block with the translation of the spans that start in it (a span merged
    across blocks belongs to the block it starts in), and the inline formatting the spans
    kept. Breaks keep a marker; blocks left without text (their sentences went to an earlier
    block's span) are left out."""
    block_of = {seg.id: seg.block_id for seg in report.segments}
    texts: dict[str, list[tuple[str, list[InlineMark]]]] = {}
    for span in report.spans:
        if span.text.strip():
            formatting = kept_formatting(span)
            marks = formatting.marks if formatting is not None else []
            texts.setdefault(block_of[span.start], []).append((span.text, marks))
    out: list[TranslatedBlock] = []
    for block in report.blocks:
        if block.kind is BlockKind.BREAK:
            out.append(TranslatedBlock(block=block, text=BREAK_MARKER))
        elif block.id in texts:
            text, marks = join_marked(texts[block.id], " ")
            out.append(TranslatedBlock(block=block, text=text, marks=marks))
    return out


def source_name(report: Report) -> PurePosixPath:
    """The source document's path, whichever separator it was uploaded with."""
    return PurePosixPath(report.document.source_path.replace("\\", "/"))


def export_basename(report: Report) -> str:
    stem = source_name(report).stem or "document"
    return f"{stem}.{report.profile.target_lang}"


def is_rtl(lang: str) -> bool:
    """Right-to-left by language or by an explicit script subtag (az-Arab)."""
    parts = lang.replace("_", "-").lower().split("-")
    return parts[0] in _RTL_LANGUAGES or any(p in _RTL_SCRIPTS for p in parts[1:])
