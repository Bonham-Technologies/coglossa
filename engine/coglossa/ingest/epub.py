"""EPUB ingestion (spec §4.1): the chapters in reading (spine) order, each parsed like HTML.
A block's locator is its chapter's path inside the EPUB. DRM-protected books are refused;
font obfuscation alone is not DRM."""

import posixpath
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from lxml import etree

from coglossa.ingest.base import IngestError, Protection, error_reason, file_sha256, make_blocks
from coglossa.ingest.html import Item, collect_marked, parse_html
from coglossa.schema import InlineMark, SourceDocument

MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_CHAPTER_TYPES = frozenset({"application/xhtml+xml", "text/html"})
# Package files come from untrusted books: no entity expansion, DTDs or network access.
_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)


class _Unreadable(ValueError):
    pass


def _xml(z: zipfile.ZipFile, name: str) -> Any:
    try:
        return etree.fromstring(z.read(name), parser=_XML_PARSER)
    except etree.XMLSyntaxError as e:
        raise _Unreadable(f"{name}: {e}") from e


def _chapters(z: zipfile.ZipFile) -> list[str]:
    container = _xml(z, "META-INF/container.xml")
    rootfiles = container.xpath("//*[local-name()='rootfile']/@full-path")
    if not rootfiles:
        raise _Unreadable("META-INF/container.xml names no package document")
    opf_path = str(rootfiles[0])
    opf = _xml(z, opf_path)
    base = posixpath.dirname(opf_path)
    manifest: dict[str, tuple[str, str]] = {}
    for item in opf.xpath("//*[local-name()='manifest']/*[local-name()='item']"):
        href = posixpath.normpath(posixpath.join(base, unquote(item.get("href", ""))))
        manifest[item.get("id", "")] = (href, item.get("media-type", ""))
    chapters = []
    for idref in opf.xpath("//*[local-name()='spine']/*[local-name()='itemref']/@idref"):
        href, media_type = manifest.get(str(idref), ("", ""))
        if media_type in _CHAPTER_TYPES:
            chapters.append(href)
    if not chapters:
        raise _Unreadable("its spine lists no chapters")
    return chapters


def _encrypted(z: zipfile.ZipFile) -> set[str]:
    if "META-INF/encryption.xml" not in z.namelist():
        return set()
    uris = _xml(z, "META-INF/encryption.xml").xpath("//*[local-name()='CipherReference']/@URI")
    return {posixpath.normpath(unquote(str(u))) for u in uris}


class EpubIngestor:
    format = "epub"
    extensions = (".epub",)

    def ingest(self, path: Path) -> SourceDocument:
        try:
            with zipfile.ZipFile(path) as z:
                if sum(info.file_size for info in z.infolist()) > MAX_UNCOMPRESSED_BYTES:
                    raise IngestError(
                        f"{path.name} expands to more than {MAX_UNCOMPRESSED_BYTES // 2**20} MB; "
                        "split the book and retry"
                    )
                chapters = _chapters(z)
                if _encrypted(z) & set(chapters):
                    raise IngestError(
                        f"{path.name} is DRM-protected; only DRM-free EPUB files can be translated"
                    )
                items: list[Item] = []
                marks: list[list[InlineMark]] = []
                protect: list[Protection] = []
                for href in chapters:
                    root = parse_html(z.read(href))
                    if root is not None:
                        chapter_items, chapter_marks, chapter_protect = collect_marked(root, href)
                        items += chapter_items
                        marks += chapter_marks
                        protect += chapter_protect
        except (IngestError, OSError):
            raise  # OSError: the registry reports "cannot be read"
        except Exception as e:  # zipfile, zlib and lxml raise many unrelated types
            raise IngestError(f"{path.name} is not a readable EPUB file ({error_reason(e)})") from e
        return SourceDocument(
            source_path=str(path),
            format=self.format,
            sha256=file_sha256(path),
            blocks=make_blocks(str(path), items, marks, protect),
        )
