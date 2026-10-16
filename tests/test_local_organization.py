"""Offline contracts for the rule-only organizer; all document bytes are in memory.

Extraction uses the real materials._extract API (no MaterialService/database).
Synthetic MaterialSource instances are reserved for invalid/empty-input contracts.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
import socket
from time import perf_counter
from xml.sax.saxutils import escape
import zipfile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from study_assistant.material_types import MaterialInfo, MaterialSource
from study_assistant.materials import _extract


@pytest.fixture
def build(monkeypatch):
    # A missing implementation is a test failure, not a silently skipped suite.
    from study_assistant.local_organization import build_local_report

    def forbidden(*args, **kwargs):
        pytest.fail("Local organization must not invoke a model or network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    from study_assistant.analysis import OnlineAnalysisClient
    monkeypatch.setattr(OnlineAnalysisClient, "complete_json", forbidden)
    return build_local_report


def _docx(paragraphs):
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = []
    for text, level in paragraphs:
        props = (f'<w:pPr><w:outlineLvl w:val="{level - 1}"/></w:pPr>'
                 if level else "")
        body.append(f'<w:p>{props}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{namespace}"><w:body>{"".join(body)}</w:body></w:document>')
    return output.getvalue()


def _pdf(*pages):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for text in pages:
        page = writer.add_blank_page(width=300, height=300)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        lines = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                 for line in text.splitlines()]
        operators = " T* ".join(f"({line}) Tj" for line in lines)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 14 TL 10 280 Td {operators} ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _document(text=None, *, suffix="md", raw=None):
    filename = f"内存资料.{suffix}"
    sources, warnings, kind = _extract(filename, raw if raw is not None else text.encode("utf-8"))
    material = MaterialInfo(
        id="memory-material", filename=filename, kind=kind,
        imported_at="2026-10-16T12:34:56+00:00", source_count=len(sources),
        character_count=sum(len(s.text) for s in sources), warnings=warnings,
    )
    return material, sources


def _nodes(outline):
    result = []
    stack = list(reversed(outline))
    while stack:
        node = stack.pop()
        result.append(node)
        stack.extend(reversed(node["children"]))
    return result


def _headings(report):
    return [node for node in _nodes(report["outline"]) if node["kind"] == "heading"]


def _contract(report, material, sources, *, basis=None):
    assert isinstance(report, dict)
    assert {"title", "material_type", "summary", "outline", "knowledge_points",
            "learning_suggestions", "warnings", "metadata"} <= report.keys()
    assert isinstance(report["title"], str) and report["title"].strip()
    assert isinstance(report["material_type"], str) and report["material_type"].strip()
    assert report["summary"] == ""
    assert report["knowledge_points"] == []
    assert report["learning_suggestions"] == []
    assert isinstance(report["warnings"], list)
    assert all(isinstance(w, str) and w.strip() for w in report["warnings"])
    nodes = _nodes(report["outline"])
    assert len(nodes) <= 600
    source_ids = {source.id for source in sources}
    reached = set()
    for node in nodes:
        assert {"title", "summary", "source_ids", "children", "origin", "kind"} <= node.keys()
        assert isinstance(node["title"], str) and 0 < len(node["title"]) <= 120
        assert node["summary"] == ""
        assert node["kind"] in {"heading", "source_segment"}
        assert node["origin"] in {"original", "inferred"}
        if node["kind"] == "heading":
            assert node["origin"] == "original"
        assert isinstance(node["children"], list)
        assert isinstance(node["source_ids"], list) and node["source_ids"]
        assert len(node["source_ids"]) == len(set(node["source_ids"]))
        assert set(node["source_ids"]) <= source_ids
        reached.update(node["source_ids"])
        for child in node["children"]:
            assert set(child["source_ids"]) <= set(node["source_ids"])
    assert reached == source_ids
    meta = report["metadata"]
    assert meta["mode"] == "local"
    assert meta["model"] == "本地规则（未调用模型）"
    assert meta["generated_at"] == material.imported_at
    assert meta["analyzed_source_count"] == len(sources)
    assert meta["total_source_count"] == len(sources)
    assert meta["referenced_source_count"] == len(reached)
    assert meta["unreferenced_source_ids"] == []
    assert meta["heading_count"] == len(_headings(report)) <= 300
    assert meta["outline_basis"] in {"headings", "source_segments", "mixed"}
    if basis is not None:
        assert meta["outline_basis"] == basis
    json.dumps(report, ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize("suffix", ["md", "txt", "docx", "pdf"])
def test_real_extracted_formats_and_warning_propagation(build, suffix):
    if suffix == "docx":
        material, sources = _document(suffix=suffix, raw=_docx([
            ("Chapter", 1), ("Section", 3), ("Body text.", None)]))
    elif suffix == "pdf":
        material, sources = _document(suffix=suffix, raw=_pdf("# Chapter\nBody text.", "## Section\nMore text."))
        assert [s.page for s in sources] == [1, 2]
    else:
        material, sources = _document("# Chapter\n\n### Section\n\nBody text.", suffix=suffix)
    report = build(material, sources)
    _contract(report, material, sources, basis="headings")
    assert [n["title"] for n in _headings(report)] == ["Chapter", "Section"]
    assert set(material.warnings) <= set(report["warnings"])


def test_markdown_levels_one_through_nine_close_hashes_and_stack_skips(build):
    text = "\n\n".join(f'{"#" * level} Level {level} ###' for level in range(1, 10))
    material, sources = _document(text)
    report = build(material, sources)
    _contract(report, material, sources, basis="headings")
    cursor = report["outline"]
    for level in range(1, 10):
        assert len(cursor) == 1
        assert cursor[0]["title"] == f"Level {level}"
        cursor = cursor[0]["children"]
    assert cursor == []

    material, sources = _document("# Root\n\n#### Deep\n\n## Middle\n\n# Next")
    report = build(material, sources)
    _contract(report, material, sources)
    assert [n["title"] for n in report["outline"]] == ["Root", "Next"]
    assert [n["title"] for n in report["outline"][0]["children"]] == ["Deep", "Middle"]
    assert len(_headings(report)) == 4  # No invented levels 2/3 before Deep.


@pytest.mark.parametrize("title", ["第一篇 基础", "第十二章 数据结构", "第三节 栈", "1.1 栈", "2.3.4 队列"])
def test_conservative_chinese_and_multilevel_numeric_headings(build, title):
    material, sources = _document(title + "\n\n正文说明。", suffix="txt")
    report = build(material, sources)
    _contract(report, material, sources, basis="headings")
    assert [n["title"] for n in _headings(report)] == [title]


@pytest.mark.parametrize("line", [
    "1. 第一项", "2 第二项", "1) 第三项", "1.1 + 2.2 = 3.3",
    "1.1 是这里使用的版本号。", "本章介绍第一章的内容。", "########## Too deep",
])
def test_lists_equations_and_body_sentences_are_not_headings(build, line):
    material, sources = _document(line + "\n\n普通正文。")
    report = build(material, sources)
    _contract(report, material, sources, basis="source_segments")
    assert _headings(report) == []


@pytest.mark.parametrize("code", [
    "```markdown\n# Fake\n第一章 假标题\n1.1 Fake\n```",
    "~~~text\n## Fake\n~~~",
    "    # Fake\n    第一章 假标题\n    1.1 Fake",
    "\t# Fake\n\t第二章 假标题",
])
def test_code_blocks_and_indented_code_are_not_headings(build, code):
    material, sources = _document(code + "\n\n# Real\n\nBody")
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert [n["title"] for n in _headings(report)] == ["Real"]
    assert report["outline"][0]["kind"] == "source_segment"


def test_cross_source_section_ranges_and_root_preface(build):
    # Real packing yields five distinct sources, not manually invented boundaries.
    paragraphs = ["前言" + "序" * 1600, "# Root\n" + "甲" * 1600,
                  "### Child\n" + "乙" * 1600, "续文" + "丙" * 1600,
                  "# Next\n" + "丁" * 1600]
    material, sources = _document("\n\n".join(paragraphs))
    assert len(sources) == 5
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    preface, root, following = report["outline"]
    assert preface["kind"] == "source_segment"
    assert preface["source_ids"] == [sources[0].id]
    assert root["title"] == "Root"
    assert set(root["source_ids"]) == {s.id for s in sources[1:4]}
    child = next(n for n in root["children"] if n["kind"] == "heading")
    assert child["title"] == "Child"
    assert set(child["source_ids"]) == {s.id for s in sources[2:4]}
    assert following["title"] == "Next"
    assert following["source_ids"] == [sources[4].id]


@pytest.mark.parametrize("suffix", ["txt", "pdf"])
def test_headingless_outline_uses_actual_source_locators(build, suffix):
    if suffix == "pdf":
        material, sources = _document(suffix="pdf", raw=_pdf("First body.", "Second body."))
    else:
        material, sources = _document("正文" * 2000, suffix=suffix)
    report = build(material, sources)
    _contract(report, material, sources, basis="source_segments")
    for source in sources:
        assert any(source.id in n["source_ids"] and source.locator in n["title"]
                   for n in _nodes(report["outline"]))


def test_heading_limit_flushes_stack_and_later_sources_are_root_segments(build):
    text = "\n\n".join(f"## Heading {i:03d}" for i in range(301))
    text += "\n\n" + "尾文" * 2000 + "\n\n# Must not resume\n\n" + "后文" * 1600
    material, sources = _document(text)
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert len(_headings(report)) == 300
    assert [n["title"] for n in _headings(report)] == [f"Heading {i:03d}" for i in range(300)]
    assert report["warnings"]
    roots = report["outline"]
    last_heading = max(i for i, n in enumerate(roots) if n["kind"] == "heading")
    assert roots[last_heading + 1:]
    assert all(n["kind"] == "source_segment" for n in roots[last_heading + 1:])
    assert any(sources[-1].id in n["source_ids"] for n in roots[last_heading + 1:])
    assert all(sources[-1].id not in n["source_ids"] for n in _headings(report))


def test_extremely_long_heading_falls_back_without_truncated_fake_heading(build):
    material, sources = _document("# " + "长" * 5000 + "\n\n# Valid\n\n正文")
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert [n["title"] for n in _headings(report)] == ["Valid"]
    assert report["outline"][0]["kind"] == "source_segment"


def test_counts_use_actual_iterable_not_stale_material_count(build):
    material, sources = _document("# Root\n\n" + "正文" * 2000)
    material = replace(material, source_count=999)
    report = build(material, sources)
    _contract(report, material, sources)
    assert report["warnings"]


def test_generator_immutability_stability_and_independent_documents(build):
    material, sources = _document("前言\n\n# First\n\n## Child\n\n正文")
    snapshot = deepcopy((material, sources))
    first = build(material, (s for s in sources))
    _contract(first, material, sources)
    assert (material, sources) == snapshot
    assert build(material, sources) == first
    other, other_sources = _document("# Other\n\n不同正文")
    other = replace(other, id="another-material", filename="other.md")
    other_report = build(other, other_sources)
    assert [n["title"] for n in _headings(other_report)] == ["Other"]
    assert build(material, sources) == first
    first["outline"][0]["title"] = "caller mutation"
    first["warnings"].append("caller warning")
    assert build(material, sources) == build(snapshot[0], snapshot[1])
    assert (material, sources) == snapshot


def test_fenced_code_state_survives_real_source_boundaries(build):
    text = "```text\n" + "x" * 2990 + "\n# Fake\n第一章 假标题\n```\n\n# Real\n正文"
    material, sources = _document(text)
    assert len(sources) >= 2
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert [n["title"] for n in _headings(report)] == ["Real"]


def test_heading_cutoff_with_300_sources_never_exceeds_total_node_budget(build):
    headings = "\n\n".join(f"# H{i:03d}" for i in range(301))
    # The heading block fits in one real source; each tail paragraph gets its own.
    material, sources = _document(headings + "\n\n" + "\n\n".join(
        "正文" * 950 for _ in range(299)))
    assert len(sources) == 300
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert len(_headings(report)) == 300
    tail = {s.id for s in sources[1:]}
    root_segments = [n for n in report["outline"] if n["kind"] == "source_segment"]
    assert tail <= {sid for n in root_segments for sid in n["source_ids"]}
    assert not any(tail.intersection(n["source_ids"]) for n in _headings(report))


@pytest.mark.parametrize("empty_text", ["", " \t\n"])
def test_all_empty_sources_still_have_references_and_warnings(build, empty_text):
    material, _ = _document("正文")
    sources = [MaterialSource("S1", empty_text, "第 1 段起", paragraph=1)]
    material = replace(material, character_count=len(empty_text))
    report = build(material, sources)
    _contract(report, material, sources, basis="source_segments")
    assert report["warnings"]


def test_empty_source_is_reachable_and_warned(build):
    material, sources = _document("# Root\n\n正文")
    empty = MaterialSource("S2", "", "第 2 段起", paragraph=2)
    sources.append(empty)
    material = replace(material, source_count=2)
    report = build(material, sources)
    _contract(report, material, sources)
    assert report["warnings"]


def test_no_sources_is_explicitly_empty_or_rejected(build):
    material, _ = _document("正文")
    material = replace(material, source_count=0, character_count=0)
    try:
        report = build(material, iter(()))
    except (ValueError, TypeError):
        return
    _contract(report, material, [], basis="source_segments")
    assert report["outline"] == []
    assert report["warnings"]


@pytest.mark.parametrize("bad_sources", [
    None, 123, "not sources", [None], [{}],
    [MaterialSource("", "body", "locator")],
    [MaterialSource("S1", None, "locator")],
    [MaterialSource("S1", "body", "locator"), MaterialSource("S1", "other", "locator")],
])
def test_invalid_sources_raise_public_input_errors(build, bad_sources):
    material, _ = _document("正文")
    with pytest.raises((ValueError, TypeError)):
        build(material, bad_sources)


@pytest.mark.parametrize("material", [None, {}, "material", 42])
def test_invalid_material_raises_public_input_error(build, material):
    _, sources = _document("正文")
    with pytest.raises((ValueError, TypeError)):
        build(material, sources)


def test_600k_characters_300_real_sources_are_bounded_and_fast(build):
    # 300 * 2000 characters, including one heading per source. Packing two
    # paragraphs would exceed SOURCE_CHARS, so this really exercises 300 sources.
    paragraphs = []
    for i in range(300):
        prefix = f"# Heading {i:03d}\n"
        paragraphs.append(prefix + "文" * (2000 - len(prefix)))
    material, sources = _document("\n\n".join(paragraphs))
    assert len(sources) == 300
    assert sum(len(s.text) for s in sources) == 600_000
    started = perf_counter()
    report = build(material, iter(sources))
    elapsed = perf_counter() - started
    _contract(report, material, sources, basis="headings")
    assert len(_headings(report)) == 300
    assert elapsed < 10.0, f"600k/300-source organization took {elapsed:.2f}s"


def test_build_has_no_file_database_network_or_model_io(build, monkeypatch):
    import builtins
    from pathlib import Path
    import sqlite3

    material, sources = _document("# Local\n\n正文")

    def forbidden(*args, **kwargs):
        pytest.fail("Local organization must not open files or databases")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    report = build(material, sources)
    _contract(report, material, sources, basis="headings")


def test_math_blocks_do_not_contain_headings(build):
    material, sources = _document("$$\n# Not a heading\n1.1 Fake\n$$\n\n# Real")
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert [n["title"] for n in _headings(report)] == ["Real"]


def test_number_and_chinese_hierarchies_skip_without_inventing_nodes(build):
    material, sources = _document(
        "第一篇 基础\n\n第一章 结构\n\n第一节 栈\n\n第二章 算法", suffix="txt")
    report = build(material, sources)
    _contract(report, material, sources, basis="headings")
    root = report["outline"][0]
    assert [n["title"] for n in root["children"]] == ["第一章 结构", "第二章 算法"]
    assert root["children"][0]["children"][0]["title"] == "第一节 栈"
    material, sources = _document("1.1 栈\n\n1.1.1.1 深层\n\n1.2 队列")
    report = build(material, sources)
    _contract(report, material, sources, basis="headings")
    assert [n["title"] for n in report["outline"]] == ["1.1 栈", "1.2 队列"]
    assert report["outline"][0]["children"][0]["title"] == "1.1.1.1 深层"


def test_worst_case_has_exactly_600_nodes(build):
    # Each overlong candidate clears the old chapter; each source then has a
    # fallback plus a genuine heading. These are real extraction boundaries.
    paragraphs = ["# " + "长" * 1800 + f"\n\n# H{i:03d}" for i in range(300)]
    material, sources = _document("\n\n".join(paragraphs))
    assert len(sources) == 300
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert len(_nodes(report["outline"])) == 600


@pytest.mark.parametrize("oversized", ["sources", "characters"])
def test_outside_import_limits_is_explicitly_rejected(build, oversized):
    material, _ = _document("body")
    sources = ([MaterialSource(f"S{i}", "body", "locator") for i in range(301)]
               if oversized == "sources" else
               [MaterialSource("S1", "x" * 600_001, "locator")])
    with pytest.raises(ValueError):
        build(material, sources)


def test_chapter_numeric_context_and_cross_source_membership(build):
    paragraphs = ["第一篇 基础\n第一章 绪论\n" + "文" * 1600,
                  "1.1 背景\n1.1.1 历史\n" + "文" * 1600,
                  "1.2 目标\n" + "文" * 1600,
                  "第二章 实现\n2.1 方法\n" + "文" * 1600]
    material, sources = _document("\n\n".join(paragraphs))
    assert len(sources) == 4
    report = build(material, sources)
    _contract(report, material, sources, basis="headings")
    part = report["outline"][0]
    first, second = part["children"]
    assert [n["title"] for n in first["children"]] == ["1.1 背景", "1.2 目标"]
    assert first["children"][0]["children"][0]["title"] == "1.1.1 历史"
    assert first["source_ids"] == [s.id for s in sources[:3]]
    assert second["children"][0]["title"] == "2.1 方法"
    assert second["source_ids"] == [sources[3].id]


def test_chapter_numeric_and_full_chinese_systems_coexist(build):
    material, sources = _document(
        "第一章 绪论\n1.1 背景\n1.1.1 历史\n1.2 目标\n"
        "第二章 结构\n第一节 容器\n2.1 栈\n第二节 算法")
    report = build(material, sources)
    _contract(report, material, sources, basis="headings")
    first, second = report["outline"]
    assert [n["title"] for n in first["children"]] == ["1.1 背景", "1.2 目标"]
    assert [n["title"] for n in second["children"]] == ["第一节 容器", "第二节 算法"]
    assert second["children"][0]["children"][0]["title"] == "2.1 栈"


def test_technical_symbols_are_titles_not_global_math_rejections(build):
    material, sources = _document(
        "第一章 C++基础\n1.1 B+树\n1.2 I/O系统\n1.3 栈\n1.1 + 2.2 = 3.3")
    report = build(material, sources)
    _contract(report, material, sources, basis="headings")
    assert [n["title"] for n in _headings(report)] == [
        "第一章 C++基础", "1.1 B+树", "1.2 I/O系统", "1.3 栈"]
    assert len(report["outline"][0]["children"]) == 3


@pytest.mark.parametrize("narrative", [
    "第一章的示例代码如下：", "第一章中使用栈", "第一章将讨论算法",
    "第一章介绍数据结构", "第一章 介绍数据结构",
])
def test_chinese_heading_requires_explicit_title_boundary(build, narrative):
    material, sources = _document("第一章\n" + narrative + "\n正文。\n第二章")
    report = build(material, sources)
    _contract(report, material, sources, basis="headings")
    assert [n["title"] for n in _headings(report)] == ["第一章", "第二章"]


@pytest.mark.parametrize("line", [" \t第一章 假标题", "  \t1.1 Fake", " \t# Fake"])
def test_mixed_tab_indent_uses_four_column_tab_stops(build, line):
    material, sources = _document(line + "\n\n# Real")
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert [n["title"] for n in _headings(report)] == ["Real"]


@pytest.mark.parametrize("fragment", ["# Fake", "```", "$$", r"\["])
def test_real_extraction_continued_code_line_cannot_open_heading_or_block(build, fragment):
    # Exactly the reported 3000-char cut, in the middle of an indented line.
    material, sources = _document("    " + "x" * 2996 + fragment + "\n\n# Real")
    assert sources[1].text.startswith(fragment)
    assert sources[0].paragraph == sources[1].paragraph
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert [n["title"] for n in _headings(report)] == ["Real"]


@pytest.mark.parametrize("opener,closer,split", [
    ("```", "```", 1), ("~~~", "~~~", 2), ("$$", "$$", 1), (r"\[", r"\]", 1),
])
def test_block_delimiters_split_inside_real_extraction_are_rejoined(build, opener, closer, split):
    # Open token straddles 3000; it must still suppress the following fake title.
    prefix = "x" * (2999 - split) + "\n"
    material, sources = _document(prefix + opener + "\n# Fake\n" + closer + "\n\n# Real")
    assert sources[0].text.endswith(opener[:split])
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert [n["title"] for n in _headings(report)] == ["Real"]


@pytest.mark.parametrize("opener,closer", [("```", "```"), ("$$", "$$"), (r"\[", r"\]")])
def test_continuation_suffix_is_not_a_block_closer(build, opener, closer):
    prefix = opener + "\n"
    text = prefix + "x" * (3000 - len(prefix)) + closer + "\n# Fake\n" + closer + "\n\n# Real"
    material, sources = _document(text)
    assert sources[1].text.startswith(closer)
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    # Display maths, like fences, requires an independent closing line.
    assert [n["title"] for n in _headings(report)] == ["Real"]


@pytest.mark.parametrize("opener,closer,split", [
    ("```", "```", 1), ("~~~", "~~~", 2), ("$$", "$$", 1), (r"\[", r"\]", 1),
])
def test_closing_block_token_can_straddle_real_source_cut(build, opener, closer, split):
    prefix = opener + "\n"
    filler = "x" * (2999 - split - len(prefix)) + "\n"
    material, sources = _document(prefix + filler + closer + "\n\n# Real")
    assert sources[0].text.endswith(closer[:split])
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert [n["title"] for n in _headings(report)] == ["Real"]


def test_changed_paragraph_after_full_slice_preserves_real_heading(build):
    # A 3000-char source alone is not proof of continuation: the next entry
    # has a new paragraph locator and its independent first line is a title.
    text = "x" * 6000 + "\n\n# Real"
    material, sources = _document(text)
    report = build(material, sources)
    _contract(report, material, sources, basis="mixed")
    assert [n["title"] for n in _headings(report)] == ["Real"]


def test_600k_continuous_line_has_no_fabricated_slice_headings(build):
    text = "    " + "x" * 2996 + ("# Fake" + "x" * 2994) * 199
    material, sources = _document(text)
    assert sum(len(s.text) for s in sources) == 600_000
    started = perf_counter()
    report = build(material, sources)
    elapsed = perf_counter() - started
    _contract(report, material, sources, basis="source_segments")
    assert not _headings(report)
    assert elapsed < 10.0, f"Continuous 600k text took {elapsed:.2f}s"
