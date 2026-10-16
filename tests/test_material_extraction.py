"""Local, real-byte extraction quality and safety contracts.

PDFs contain actual Helvetica text streams/blank pages; DOCX files are ZIP/XML
packages, not mocked readers. These tests do NOT test OCR, scanned-page recognition,
complex PDF rendering, or general PDF reading-order correctness. No network needed.
"""

from io import BytesIO
import re
import zipfile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

import study_assistant.materials as materials
from study_assistant.materials import MaterialService


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


@pytest.fixture
def service(tmp_path):
    return MaterialService(tmp_path / "extraction")


def _pdf(*pages):
    """None is a genuine blank page; strings become real PDF text operators."""
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for text in pages:
        page = writer.add_blank_page(width=300, height=300)
        if text is None:
            continue
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


def _paragraph(text, style=None):
    # All callers supply fixed XML-safe literals, not user input.
    properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{properties}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _document(body):
    return f'<w:document xmlns:w="{W}" xmlns:m="{M}"><w:body>{body}</w:body></w:document>'


def _docx(body=None, *, styles=None, document=None, extra=()):
    """Minimal local OPC ZIP with real document/styles XML and relationships."""
    parts = [
        ("[Content_Types].xml", (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '</Types>'
        )),
        ("_rels/.rels", (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>'
        )),
        ("word/_rels/document.xml.rels", (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdStyles" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>'
        )),
        ("word/document.xml", document if document is not None else _document(body or "")),
        ("word/styles.xml", styles if styles is not None else f'<w:styles xmlns:w="{W}"/>'),
        *extra,
    ]
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts:
            archive.writestr(name, content)
    return output.getvalue()


def _import(service, filename, raw):
    result = service.import_document(filename, raw)
    sources = service.get_sources(result.material.id)
    assert sources
    assert service.get_raw(result.material.id) == raw
    assert all(0 < len(source.text) <= 3000 for source in sources)
    if not filename.endswith(".pdf"):
        assert all(source.page is None for source in sources)
    return result, sources


def _reject(service, filename, raw, match=None):
    with pytest.raises(ValueError, match=match):
        service.import_document(filename, raw)
    assert service.list_materials() == []


def _assert_pdf_caveat(warnings):
    # Wording is not fixed, but one per-document warning must cover all three risks.
    caveats = [warning for warning in warnings if "公式" in warning and "图表" in warning]
    assert len(caveats) == 1
    assert re.search(r"阅读顺序|读取顺序|文本顺序", caveats[0])


def test_real_pdf_text_and_blank_page_have_locators_and_explicit_no_ocr(service):
    result, sources = _import(service, "mixed.pdf", _pdf("First page", None, "Last page"))
    assert [(source.text, source.page) for source in sources] == [("First page", 1), ("Last page", 3)]
    assert [source.locator for source in sources] == ["第 1 页", "第 3 页"]
    _assert_pdf_caveat(result.warnings)
    blank_warnings = [warning for warning in result.warnings if "第 2 页" in warning]
    assert len(blank_warnings) == 1
    assert "OCR" in blank_warnings[0].upper()
    assert re.search(r"不支持|未进行|未执行|未启用|不进行|没有进行", blank_warnings[0])


def test_pdf_general_caveat_is_once_per_document_not_per_page(service):
    result, sources = _import(service, "text.pdf", _pdf("One", "Two", "Three"))
    assert [source.text for source in sources] == ["One", "Two", "Three"]
    _assert_pdf_caveat(result.warnings)
    assert len(result.warnings) == 1


@pytest.mark.parametrize("raw", [b"", b"%PDF-1.7\ntruncated", _pdf(), _pdf(None, None)],
                         ids=["empty-upload", "damaged", "zero-pages", "all-blank"])
def test_empty_or_damaged_pdf_is_rejected_atomically(service, raw):
    _reject(service, "empty.pdf", raw)


def test_real_image_only_pdf_is_rejected_without_ocr(service):
    # A real raster XObject with no text layer; not an OCR accuracy test.
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    image = DecodedStreamObject()
    image.set_data(b"\x00\x00\x00")
    image.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(1),
        NameObject("/Height"): NumberObject(1),
        NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
        NameObject("/BitsPerComponent"): NumberObject(8),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): writer._add_object(image)}),
    })
    stream = DecodedStreamObject()
    stream.set_data(b"q 200 0 0 200 10 10 cm /Im1 Do Q")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    _reject(service, "scan.pdf", output.getvalue(), match="OCR")


def test_docx_heading1_and_custom_style_based_on_heading2(service):
    styles = f'''<w:styles xmlns:w="{W}">
      <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
      <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>
      <w:style w:type="paragraph" w:styleId="CourseSection">
        <w:name w:val="Course Section"/><w:basedOn w:val="Heading2"/>
      </w:style></w:styles>'''
    body = _paragraph("Chapter", "Heading1") + _paragraph("Section", "CourseSection") + _paragraph("Body")
    result, sources = _import(service, "headings.docx", _docx(body, styles=styles))
    assert [source.text for source in sources] == ["# Chapter\n\n## Section\n\nBody"]
    assert result.warnings == []


def test_docx_body_table_and_following_paragraph_keep_order_without_duplicates(service):
    body = (_paragraph("Before") + "<w:tbl><w:tr><w:tc>" + _paragraph("Cell A")
            + _paragraph("Cell B") + "</w:tc></w:tr></w:tbl>" + _paragraph("After"))
    result, sources = _import(service, "table.docx", _docx(body))
    assert [source.text for source in sources] == ["Before\n\nCell A\n\nCell B\n\nAfter"]
    assert result.warnings == []


def test_docx_nested_textbox_paragraph_is_not_counted_twice(service):
    body = ('<w:p><w:r><w:t>Outer before</w:t></w:r><w:r><w:pict>'
            '<v:shape xmlns:v="urn:schemas-microsoft-com:vml"><v:textbox><w:txbxContent>'
            + _paragraph("Textbox paragraph")
            + '</w:txbxContent></v:textbox></v:shape></w:pict></w:r>'
            '<w:r><w:t>Outer after</w:t></w:r></w:p>' + _paragraph("Following paragraph"))
    _, sources = _import(service, "textbox.docx", _docx(body))
    text = "\n\n".join(source.text for source in sources)
    # Textbox placement relative to the outer run is deliberately not prescribed.
    for token in ("Outer before", "Outer after", "Textbox paragraph", "Following paragraph"):
        assert text.count(token) == 1
    assert text.index("Textbox paragraph") < text.index("Following paragraph")


def test_docx_alternate_content_does_not_duplicate_fallback(service):
    body = ('<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
            '<mc:Choice Requires="w">' + _paragraph("One representation") + '</mc:Choice>'
            '<mc:Fallback>' + _paragraph("One representation") + '</mc:Fallback></mc:AlternateContent>')
    _, sources = _import(service, "alternate.docx", _docx(body))
    assert sources[0].text == "One representation"


def test_docx_outline_levels_and_style_cycle_are_safe(service):
    styles = (f'<w:styles xmlns:w="{W}"><w:style w:styleId="Cycle">'
              '<w:basedOn w:val="Cycle"/></w:style></w:styles>')
    body = ('<w:p><w:pPr><w:outlineLvl w:val="2"/></w:pPr>'
            '<w:r><w:t>Subsection</w:t></w:r></w:p>' + _paragraph("Body", "Cycle"))
    _, sources = _import(service, "outline.docx", _docx(body, styles=styles))
    assert sources[0].text == "### Subsection\n\nBody"


def test_docx_deleted_text_tabs_and_breaks_do_not_leak(service):
    body = ('<w:p><w:r><w:t>Visible</w:t></w:r>'
            '<w:del w:id="1"><w:r><w:delText>SECRET</w:delText>'
            '<w:t>HIDDEN</w:t><w:tab/><w:br/></w:r></w:del>'
            '<w:r><w:t>End</w:t><w:tab/><w:t>Tab</w:t><w:br/><w:t>Line</w:t></w:r></w:p>')
    result, sources = _import(service, "revision.docx", _docx(body))
    assert [source.text for source in sources] == ["VisibleEnd\tTab\nLine"]
    assert any("删除" in warning for warning in result.warnings)
    assert all("公式" not in warning and "图片" not in warning for warning in result.warnings)


@pytest.mark.parametrize("element,term", [
    ('<w:r><w:drawing/></w:r>', "图片"),
    ('<m:oMath><m:r><m:t>x=1</m:t></m:r></m:oMath>', "公式"),
], ids=["drawing-marker", "equation-marker"])
def test_docx_warns_only_when_image_or_formula_markup_is_present(service, element, term):
    # Structural presence detection only; this does not test image rendering.
    body = '<w:p><w:r><w:t>Readable body</w:t></w:r>' + element + '</w:p>'
    result, _ = _import(service, "special.docx", _docx(body))
    assert any(term in warning for warning in result.warnings)
    other_term = "公式" if term == "图片" else "图片"
    assert not any(other_term in warning for warning in result.warnings)


@pytest.mark.parametrize("suffix", ["txt", "md"])
@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-16-be-bom", "gb18030"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
def test_text_encoding_newlines_and_paragraph_gaps(service, suffix, encoding, newline):
    expected = "第一段中文\n段内换行\n\n第二段中文"
    text = expected.replace("\n", newline)
    raw = b"\xfe\xff" + text.encode("utf-16-be") if encoding == "utf-16-be-bom" else text.encode(encoding)
    result, sources = _import(service, f"notes.{suffix}", raw)
    assert [source.text for source in sources] == [expected]
    assert result.material.kind == suffix
    assert result.warnings == []


def test_markdown_indented_code_is_not_stripped(service):
    text = "# Heading\n\n    indented code\n    second line\n\nBody"
    _, sources = _import(service, "code.md", text.encode())
    assert sources[0].text == text


def test_markdown_heading_list_and_code_are_preserved(service):
    text = "# 标题\n\n## 小节\n\n- 第一项\n- 第二项\n\n```python\nprint('hello')\n```"
    result, sources = _import(service, "notes.md", text.encode("utf-8"))
    assert [source.text for source in sources] == [text]
    assert result.warnings == []


@pytest.mark.parametrize("suffix", ["txt", "md", "docx"])
def test_long_text_sources_are_bounded_and_lossless(service, suffix):
    text = "学习内容abc123" * 1100
    raw = _docx(_paragraph(text)) if suffix == "docx" else text.encode("utf-8")
    result, sources = _import(service, f"long.{suffix}", raw)
    assert len(sources) > 1
    assert "".join(source.text for source in sources) == text
    assert [source.id for source in sources] == [f"S{i}" for i in range(1, len(sources) + 1)]
    assert result.warnings == []


@pytest.mark.parametrize("suffix", ["txt", "md", "docx"])
def test_paragraph_separator_counts_toward_source_bound(service, suffix):
    paragraphs = ["a" * 1500, "b" * 1499]
    raw = (_docx("".join(_paragraph(text) for text in paragraphs)) if suffix == "docx"
           else "\n\n".join(paragraphs).encode("utf-8"))
    # 2999 body characters plus the two-character separator cannot fit in 3000.
    _, sources = _import(service, f"boundary.{suffix}", raw)
    assert [source.text for source in sources] == paragraphs


@pytest.mark.parametrize("suffix", ["txt", "md"])
@pytest.mark.parametrize("raw", [b"", b" \t\r\n\r\n", b"\x00\x01\x02\x03\xff\xfe\x80\x81"],
                         ids=["empty", "whitespace", "damaged-binary"])
def test_empty_whitespace_or_binary_text_is_rejected(service, suffix, raw):
    _reject(service, f"invalid.{suffix}", raw)


@pytest.mark.parametrize("raw", [_docx(), b"not a ZIP archive", _docx(document="<broken")],
                         ids=["empty-body", "broken-zip", "malformed-xml"])
def test_empty_or_corrupt_docx_is_rejected(service, raw):
    _reject(service, "invalid.docx", raw)


@pytest.mark.parametrize("limit", ["MAX_UNCOMPRESSED_BYTES", "MAX_ZIP_MEMBERS"])
def test_docx_archive_limits_with_small_thresholds(service, monkeypatch, limit):
    raw = _docx(_paragraph("Small but above the patched limit"))
    monkeypatch.setattr(materials, limit, 1)
    _reject(service, "oversized.docx", raw, match="超过|限制")


@pytest.mark.parametrize("part", ["word/document.xml", "word/styles.xml"])
def test_docx_duplicate_critical_xml_members_are_rejected(service, part):
    duplicate = _document(_paragraph("Duplicate body")) if part.endswith("document.xml") else f'<w:styles xmlns:w="{W}"/>'
    with pytest.warns(UserWarning, match="Duplicate name"):
        raw = _docx(_paragraph("Original body"), extra=[(part, duplicate)])
    _reject(service, "duplicate.docx", raw)


@pytest.mark.parametrize("part", ["document", "styles"])
def test_docx_dtd_is_rejected_even_for_local_internal_entities(service, part):
    # No external entity or network access: a tiny internal DTD must also be refused.
    if part == "document":
        xml = '<!DOCTYPE w:document [<!ENTITY local "DTD_SENTINEL">]>' + _document(_paragraph("&local;"))
        raw = _docx(document=xml)
    else:
        xml = (f'<!DOCTYPE w:styles [<!ENTITY local "DTD_SENTINEL">]>'
               f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:styleId="Custom">'
               '<w:name w:val="&local;"/></w:style></w:styles>')
        raw = _docx(_paragraph("Body", "Custom"), styles=xml)
    _reject(service, "dtd.docx", raw)


def test_existing_storage_is_read_without_reextracting_or_migrating(service):
    import hashlib
    import sqlite3

    raw = b"old raw content"
    with sqlite3.connect(service.db_path) as db:
        db.execute("INSERT INTO materials VALUES(?,?,?,?,?,?,?,?)", (
            "legacy", "old.txt", "txt", "2025-01-01", hashlib.sha256(raw).hexdigest(),
            raw, 10, '["old warning"]'))
        db.execute("INSERT INTO material_sources VALUES(?,?,?,?,?,?)", (
            "legacy", "S1", "alpha\nbeta", "old locator", None, 1))
    restarted = MaterialService(service.data_dir)
    source = restarted.get_sources("legacy")[0]
    assert (source.text, source.locator, source.page, source.paragraph) == ("alpha\nbeta", "old locator", None, 1)
    duplicate = restarted.import_document("copy.txt", raw)
    assert duplicate.duplicate
    assert duplicate.material.id == "legacy"
    assert duplicate.warnings == ["old warning"]
    assert duplicate.material.character_count == 10


def test_documents_remain_independent(service):
    first, first_sources = _import(service, "first.md", b"# First\n\nOne")
    second, second_sources = _import(service, "second.txt", b"Second\n\nTwo")
    assert first.material.id != second.material.id
    assert [source.id for source in first_sources] == ["S1"]
    assert [source.id for source in second_sources] == ["S1"]
    assert service.get_sources(first.material.id) == first_sources
    assert service.get_sources(second.material.id) == second_sources
