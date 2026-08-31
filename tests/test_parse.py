from __future__ import annotations

from xhnovel_pipeline.parse import parse_html, parse_text


ARTIFACT_ID = "sha256:" + "0" * 64


def test_single_newline_text_has_original_character_offsets_per_line():
    raw = "标题\r\n第一段\r\n第二段"
    parsed = parse_text(
        ARTIFACT_ID,
        raw.encode("utf-8"),
        document_id="DOC-TEXTLOCATOR",
    )

    assert [item["normalized_text"] for item in parsed["segments"]] == [
        "标题",
        "第一段",
        "第二段",
    ]
    locators = [item["source_locator"] for item in parsed["segments"]]
    assert [item["selector"] for item in locators] == ["line:1", "line:2", "line:3"]
    assert [(item["start"], item["end"]) for item in locators] == [(0, 2), (4, 7), (9, 12)]
    assert [raw[item["start"] : item["end"]] for item in locators] == [
        "标题",
        "第一段",
        "第二段",
    ]


def test_html_locators_preserve_each_actual_block_tag():
    html = (
        b"<html><body><h1>Head</h1><ul><li>One</li><li>Two</li></ul>"
        b"<div>Box</div><p>Para</p></body></html>"
    )
    parsed = parse_html(ARTIFACT_ID, html, document_id="DOC-HTMLLOCATOR")

    assert [item["normalized_text"] for item in parsed["segments"]] == [
        "Head",
        "One",
        "Two",
        "Box",
        "Para",
    ]
    assert [item["source_locator"]["selector"] for item in parsed["segments"]] == [
        "h1:nth-of-type(1)",
        "li:nth-of-type(1)",
        "li:nth-of-type(2)",
        "div:nth-of-type(1)",
        "p:nth-of-type(1)",
    ]
