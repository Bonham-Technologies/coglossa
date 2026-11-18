from datetime import datetime, timedelta, timezone

import pytest
from lxml import etree

import coglossa
from coglossa.export.tmx import tmx_bytes, tmx_lang
from tests.export.conftest import make_report, make_span

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _parse(data: bytes) -> etree._Element:
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))


def _units(root) -> list[tuple[str | None, str | None, list[tuple[str, str]]]]:
    return [
        (
            tu.get("tuid"),
            tu.get("segtype"),
            [(tuv.get(XML_LANG), tuv.findtext("seg")) for tuv in tu.findall("tuv")],
        )
        for tu in root.find("body")
    ]


def test_one_unit_per_span_with_its_source_and_translation(report):
    data = tmx_bytes(report)
    assert data.startswith(b"<?xml version='1.0' encoding='UTF-8'?>\n<!DOCTYPE tmx SYSTEM")
    root = _parse(data)
    assert root.tag == "tmx" and root.get("version") == "1.4"
    header = root.find("header")
    assert dict(header.attrib) == {
        "creationtool": "Coglossa",
        "creationtoolversion": coglossa.__version__,
        "segtype": "block",  # p2 covers two sentences
        "o-tmf": "Coglossa",
        "adminlang": "en",
        "srclang": "fr",
        "datatype": "plaintext",
        "creationdate": "20261011T000000Z",
    }
    assert _units(root) == [
        ("p1", "sentence", [("fr", "Chapitre un"), ("en", "Chapter One")]),
        ("p2", "block", [("fr", "Le chat dort. Il pleut."), ("en", "The cat sleeps.\nIt rains.")]),
        ("p3", "sentence", [("fr", "Fin <du> & monde."), ("en", "The end <of> & world.")]),
    ]
    assert b"Fin &lt;du&gt; &amp; monde." in data  # escaped by the XML library


def test_sentence_spans_make_a_sentence_memory():
    report = make_report(
        spans=[
            make_span("p1", "s1", "s1", "Chapter One"),
            make_span("p2", "s2", "s2", "The cat sleeps."),
        ]
    )
    root = _parse(tmx_bytes(report))
    assert root.find("header").get("segtype") == "sentence"


def test_only_the_approved_passages_with_the_text_a_person_approved(report):
    root = _parse(tmx_bytes(report, approved={"p2": "The cat is asleep. It rains.", "p9": "x"}))
    assert _units(root) == [
        (
            "p2",
            "block",
            [("fr", "Le chat dort. Il pleut."), ("en", "The cat is asleep. It rains.")],
        ),
    ]


def test_nothing_approved_is_an_empty_but_valid_memory(report):
    root = _parse(tmx_bytes(report, approved={}))
    assert root.find("body") is not None and len(root.find("body")) == 0
    assert root.find("header").get("segtype") == "sentence"


def test_characters_xml_cannot_hold_are_dropped_and_blank_passages_left_out():
    report = make_report(
        spans=[
            make_span("p1", "s1", "s1", "Chap\x00ter\x0b One￾ \U0001f408"),
            make_span("p2", "s2", "s3", " \x01 "),
            make_span("p3", "s4", "s4", "الفصل الأول‏"),
        ]
    )
    report.segments[0].text = "Chapitre\x1f un"
    report.profile.target_lang = "ar"
    root = _parse(tmx_bytes(report))
    assert _units(root) == [
        ("p1", "sentence", [("fr", "Chapitre un"), ("ar", "Chapter One \U0001f408")]),
        ("p3", "sentence", [("fr", "Fin <du> & monde."), ("ar", "الفصل الأول‏")]),
    ]


def test_the_creation_date_is_in_utc(report):
    report.created_at = datetime(2026, 10, 11, 9, 30, 5, tzinfo=timezone(timedelta(hours=10)))
    assert _parse(tmx_bytes(report)).find("header").get("creationdate") == "20261010T233005Z"
    report.created_at = datetime(2026, 10, 11, 9, 30, 5)  # naive: taken as UTC
    assert _parse(tmx_bytes(report)).find("header").get("creationdate") == "20261011T093005Z"


@pytest.mark.parametrize(
    ("stored", "tag"),
    [
        ("en", "en"),
        ("FR", "fr"),
        ("pt_br", "pt-BR"),
        ("zh_hans", "zh-Hans"),
        ("ZH-hant-tw", "zh-Hant-TW"),
        ("es-419", "es-419"),
        ("sr-latn-RS", "sr-Latn-RS"),
        ("de-CH-1996", "de-CH-1996"),
        ("en-x-Pirate", "en-x-pirate"),
        (" ar ", "ar"),
        ("", "und"),
        ("unknown language", "und"),
        ("e", "und"),
    ],
)
def test_language_codes_become_bcp47_tags(stored, tag):
    assert tmx_lang(stored) == tag
