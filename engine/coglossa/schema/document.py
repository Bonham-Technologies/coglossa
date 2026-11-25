"""Source documents after ingestion, and their segmentation."""

from enum import StrEnum

from pydantic import BaseModel, Field


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    BREAK = "break"  # scene/section break marker; carries no translatable text
    LIST_ITEM = "list_item"
    TABLE_CELL = "table_cell"
    FOOTNOTE = "footnote"


class Region(StrEnum):
    """The part of a file a block comes from; blocks without one are body text."""

    HEADER = "header"
    FOOTER = "footer"
    FOOTNOTE = "footnote"
    ENDNOTE = "endnote"


class MarkKind(StrEnum):
    """Inline formatting an export can put back on the translated words (CGL-108)."""

    BOLD = "bold"
    ITALIC = "italic"
    UNDERLINE = "underline"
    SUPERSCRIPT = "superscript"
    SUBSCRIPT = "subscript"
    LINK = "link"


class KeepReason(StrEnum):
    """Why a block is left as written, never sent to a model (CGL-111)."""

    CODE = "code"  # a Markdown fenced code block, an HTML <pre> or code-only block
    NO_TRANSLATE = "no_translate"  # HTML marked translate="no" (or class="notranslate")


class InlineMark(BaseModel):
    """One kind of inline formatting over `text[start:end]` of a block or a passage."""

    start: int
    end: int
    kind: MarkKind
    href: str | None = None  # a link's target


class TableCellPosition(BaseModel):
    """Where a table cell sits, so an export can rebuild the table (CGL-87). Rows and columns
    count the table's grid from 1; a merged cell is placed at its top-left corner."""

    table: int  # the table's number in reading order; nested tables are numbered too
    rows: int
    cols: int
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    parent: "TableCellPosition | None" = None  # the cell a nested table sits in


class Provenance(BaseModel):
    source_path: str
    index: int  # ordinal of the block within the source file
    page: int | None = None
    locator: str | None = None  # format-specific, e.g. "table 2 row 1 col 3"
    # Structure an export can rebuild (CGL-87, DOCX only so far). Every field is optional, so
    # blocks stored before them still load.
    level: int | None = None  # a heading's level: 0 for a title, 1-9 for Heading 1-9
    region: Region | None = None  # None: body text
    cell: TableCellPosition | None = None  # a table cell's position
    note: str | None = None  # the mark of the footnote or endnote this block is part of
    note_refs: list[str] = Field(default_factory=list)  # marks of the notes this block cites
    # Inline formatting of the block's text (CGL-108: DOCX, HTML and EPUB); none in older blocks
    marks: list[InlineMark] = Field(default_factory=list)
    # CGL-111: a block left as written (its text is carried through untranslated), and the
    # text inside a translated block that the file marks as code or not to translate, one entry
    # per occurrence, which the literals check expects in the translation as written
    keep: KeepReason | None = None
    literals: list[str] = Field(default_factory=list)


class Block(BaseModel):
    id: str  # "b1", "b2", … in reading order
    kind: BlockKind
    text: str
    provenance: Provenance


class SourceDocument(BaseModel):
    source_path: str
    format: str
    sha256: str
    blocks: list[Block]
    # plain sentences about content the ingestor could not read and left out (CGL-87)
    notices: list[str] = Field(default_factory=list)


class Segment(BaseModel):
    id: str  # "s1", "s2", … document-global, in reading order
    block_id: str
    start: int  # character offset into the block's text
    end: int
    text: str  # == block.text[start:end]


class Chunk(BaseModel):
    id: str  # "c1", "c2", …
    segment_ids: list[str]  # contiguous, in reading order
