import re

from coglossa.export.html import render_html
from coglossa.schema import Flag, FlagCode
from coglossa.scoring.untranslated import untranslated_check
from tests.export.conftest import make_report, make_span


def test_html_is_self_contained(report):
    page = render_html(report)
    assert page.startswith('<!doctype html>\n<html lang="en">')
    for forbidden in ("<script", "<link", "http://", "https://", "@import", "url("):
        assert forbidden not in page
    assert '<meta charset="utf-8">' in page


def test_html_escapes_text_and_marks_direction(report):
    page = render_html(report)
    assert "Fin &lt;du&gt; &amp; monde." in page
    assert "The end &lt;of&gt; &amp; world." in page
    assert "<of>" not in page
    assert "The cat sleeps.<br>It rains." in page
    # every source and translation cell lets the browser pick the direction
    assert page.count('<td dir="auto" lang="fr">') == 3
    assert page.count('<td dir="auto" lang="en">') == 3


def test_html_lists_open_flags_scores_and_notices(report):
    page = render_html(report)
    assert "<b>low_meaning</b>: Meaning score 55 is below 70." in page
    assert "style_drift" not in page  # dismissed
    rows = re.findall(r'<tr id="p(\d)"( class="flagged")?>', page)
    assert rows == [("1", ""), ("2", ' class="flagged"'), ("3", "")]
    assert "<td>92</td><td>88</td>" in page
    assert "<td>55</td><td>70</td>" in page
    assert "<td>—</td><td>—</td>" in page
    assert "Scores for fr→en are less reliable — review more broadly." in page
    assert "A cat sleeps while it rains." in page
    assert "<title>story.txt (fr → en)</title>" in page
    assert "3 passages · 1 open flag" in page


def test_html_without_summary_or_notices():
    report = make_report()
    report.guide.summary = ""
    report.notices = []
    page = render_html(report)
    assert "<h2>Summary</h2>" not in page
    assert 'class="notice"' not in page


def test_html_lists_a_passage_left_untranslated():
    found = untranslated_check("Le chat dort. Il pleut.", "Le chat dort. Il pleut.", "en")
    flag = Flag(
        code=FlagCode.UNTRANSLATED, explanation=found.explanation(), evidence=found.as_evidence()
    )
    report = make_report(
        spans=[
            make_span("p1", "s1", "s1", "Chapter One"),
            make_span("p2", "s2", "s3", "Le chat dort. Il pleut.", flags=[flag]),
            make_span("p3", "s4", "s4", "The end."),
        ]
    )
    page = render_html(report)
    assert (
        "<b>untranslated</b>: This passage may not have been translated: the translation is the "
        "same as the original." in page
    )
    assert '<tr id="p2" class="flagged">' in page
