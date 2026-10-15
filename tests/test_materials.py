from io import BytesIO
import zipfile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from study_assistant.materials import MaterialService, SOURCE_CHARS


def pdf_bytes(pages):
    writer = PdfWriter()
    for _ in pages:
        writer.add_blank_page(width=300, height=300)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def pdf_with_text_pages(texts):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for text in texts:
        page = writer.add_blank_page(width=300, height=300)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        stream = DecodedStreamObject()
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(f"BT /F1 12 Tf 10 100 Td ({safe}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def docx_bytes(paragraphs):
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    before = f"<w:p><w:r><w:t>{paragraphs[0]}</w:t></w:r></w:p>"
    table = (f"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>{paragraphs[1]}</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
             if len(paragraphs) > 2 else "")
    after = "".join("<w:p><w:r><w:t>" + text + "</w:t></w:r></w:p>" for text in paragraphs[2:])
    body = before + table + after
    xml = f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'.encode()
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return stream.getvalue()


def docx_with_revision_and_table():
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = (
        '<w:p><w:r><w:t>before</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell one</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>cell two</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '<w:p><w:del><w:r><w:delText>removed text</w:delText></w:r></w:del>'
        '<w:r><w:t>after</w:t></w:r></w:p>'
    )
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>')
    return stream.getvalue()


@pytest.fixture
def service(tmp_path):
    return MaterialService(tmp_path)


def test_text_formats_decode_split_and_preserve_source_order(service):
    imported = service.import_document("notes.txt", ("第一段\n\n" + "x" * 6500).encode("utf-8"))
    assert imported.material.kind == "txt"
    assert imported.material.source_count == 4
    sources = service.get_sources(imported.material.id)
    assert [s.id for s in sources] == ["S1", "S2", "S3", "S4"]
    assert all(len(s.text) <= SOURCE_CHARS for s in sources)
    assert "第一段" in sources[0].text
    assert "x" * 500 in "".join(s.text for s in sources)
    assert service.import_document("readme.md", "标题\n\n内容".encode()).material.kind == "md"
    assert service.import_document("legacy.txt", "中文内容".encode("gb18030")).material.character_count == 4


def test_docx_paragraphs_and_table_paragraphs_are_in_xml_order(service):
    raw = docx_bytes(["before", "table cell", "after"])
    result = service.import_document("lesson.docx", raw)
    sources = service.get_sources(result.material.id)
    assert "before" in sources[0].text
    assert "table cell" in sources[0].text
    assert "after" in sources[0].text
    assert sources[0].paragraph == 1
    assert sources[0].locator == "第 1 段起"


def test_docx_preserves_table_order_and_ignores_deleted_revision(service):
    result = service.import_document("revised.docx", docx_with_revision_and_table())
    source = service.get_sources(result.material.id)[0]
    assert source.text.index("before") < source.text.index("cell one") < source.text.index("cell two") < source.text.index("after")
    assert "removed text" not in source.text
    assert result.warnings == ["已忽略 DOCX 中标记为删除的修订内容。"]
    assert source.paragraph == 1


def test_real_pdf_text_extraction_keeps_short_page_and_page_locators(service):
    result = service.import_document("real-synthetic.pdf", pdf_with_text_pages(["Hi", "Second page text"]))
    sources = service.get_sources(result.material.id)
    assert [source.page for source in sources] == [1, 2]
    assert [source.locator for source in sources] == ["第 1 页", "第 2 页"]
    assert sources[0].text == "Hi"
    assert sources[1].text == "Second page text"


def test_pdf_retains_short_and_mixed_empty_pages(monkeypatch, service):
    import pypdf

    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class Reader:
        is_encrypted = False

        def __init__(self, _raw, strict=True):
            self.pages = [Page("短"), Page(""), Page("readable text")]

    monkeypatch.setattr(pypdf, "PdfReader", Reader)
    result = service.import_document("mixed.pdf", pdf_bytes([1, 2, 3]))
    assert result.material.source_count == 2
    assert result.material.character_count == 14
    assert result.material.warnings == ["第 2 页没有可读取的文字，可能是扫描页或空白页。"]
    assert [(s.page, s.paragraph) for s in service.get_sources(result.material.id)] == [(1, 1), (3, 1)]


def test_pdf_all_scanned_pages_rejected_without_partial_import(monkeypatch, service):
    import pypdf

    class Page:
        def extract_text(self):
            return ""

    class Reader:
        is_encrypted = False
        pages = [Page(), Page()]

        def __init__(self, _raw, strict=True):
            pass

    monkeypatch.setattr(pypdf, "PdfReader", Reader)
    with pytest.raises(ValueError, match="没有可读取的文字"):
        service.import_document("scanned.pdf", pdf_bytes([1, 2]))
    assert service.list_materials() == []


def test_invalid_inputs_dedup_restart_analysis_and_cascade(tmp_path):
    service = MaterialService(tmp_path)
    text = b"alpha\n\nbeta"
    first = service.import_document("../unsafe?.txt", text)
    assert first.material.filename == "unsafe_.txt"
    duplicate = service.import_document("copy.txt", text)
    assert duplicate.duplicate and duplicate.material.id == first.material.id
    assert service.get_raw(first.material.id) == text
    service.save_analysis(first.material.id, {"summary": "ok", "outline": ["A"]})
    restarted = MaterialService(tmp_path)
    assert restarted.get_analysis(first.material.id) == {"summary": "ok", "outline": ["A"]}
    restarted.delete_material(first.material.id)
    assert restarted.list_materials() == []
    assert restarted.import_document("copy.txt", text).duplicate is False
    for method, args in ((restarted.get_material, ("bad",)), (restarted.get_sources, ("bad",)),
                         (restarted.get_raw, ("bad",)), (restarted.get_analysis, ("bad",))):
        with pytest.raises(ValueError):
            method(*args)
    with pytest.raises(ValueError, match="文件类型不受支持"):
        restarted.import_document("paper.pdf.exe", b"x")
    with pytest.raises(ValueError, match="DOCX 文件损坏"):
        restarted.import_document("bad.docx", b"not zip")
    with pytest.raises(ValueError, match="不能超过 20 MB"):
        restarted.import_document("large.txt", b"x" * (20 * 1024 * 1024 + 1))


def test_encrypted_pdf_and_docx_archive_guards(service):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret")
    output = BytesIO()
    writer.write(output)
    with pytest.raises(ValueError, match="带密码"):
        service.import_document("locked.pdf", output.getvalue())

    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../word/document.xml", "x")
    with pytest.raises(ValueError, match="不安全的路径"):
        service.import_document("unsafe.docx", stream.getvalue())


def test_text_and_source_limits_are_checked_and_failed_report_leaves_state(monkeypatch, service):
    import study_assistant.materials as materials

    with pytest.raises(ValueError, match="文字超过 600,000 个字符"):
        service.import_document("too-many-chars.txt", ("x" * 600_001).encode())
    monkeypatch.setattr(materials, "MAX_CHARACTERS", 1_000_000)
    with pytest.raises(ValueError, match="最多生成 300 个来源片段"):
        service.import_document("too-many-sources.txt", (("x" * SOURCE_CHARS + "\n\n") * 301).encode())
    material = service.import_document("ok.txt", b"persist")
    with pytest.raises(ValueError):
        service.save_analysis(material.material.id, {"bad": float("nan")})
    assert service.get_analysis(material.material.id) is None


def test_joined_source_newlines_count_toward_character_limit(service):
    # The body has 300,300 letters; source assembly adds another newline between
    # each paragraph, taking the persisted source text just over 600,000 chars.
    raw = ("\n\n".join(["x"] * 300_300)).encode()
    with pytest.raises(ValueError, match="文字超过 600,000 个字符"):
        service.import_document("joined.txt", raw)
    assert service.list_materials() == []


def test_long_safe_filename_retains_supported_suffix(service):
    filename = "课件" + "a" * 220 + ".txt"
    result = service.import_document(filename, b"readable text")
    assert result.material.kind == "txt"
    assert result.material.filename.endswith(".txt")
    assert len(result.material.filename) <= 180


def test_pdf_page_iteration_failure_is_clear_and_atomic(monkeypatch, service):
    import pypdf

    class Reader:
        is_encrypted = False

        def __init__(self, _raw, strict=True):
            pass

        @property
        def pages(self):
            raise RuntimeError("malformed page tree")

    monkeypatch.setattr(pypdf, "PdfReader", Reader)
    with pytest.raises(ValueError, match="PDF 文件损坏或无法读取"):
        service.import_document("damaged.pdf", b"synthetic")
    assert service.list_materials() == []
