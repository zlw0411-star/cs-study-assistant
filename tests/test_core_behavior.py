from io import BytesIO

import pytest

from study_assistant import StudyAssistant
from conftest import FakeEmbedder, FakeGenerator


def test_empty_database_search_and_ask(assistant):
    assert assistant.list_documents() == []
    assert assistant.search("deadlock", method="bm25") == []
    answer = assistant.ask("deadlock", method="bm25")
    assert answer.hits == [] and answer.citations == [] and answer.error is None


def test_bm25_exact_single_document_match_and_nonmatch(assistant):
    assistant.import_document("only.txt", b"Semaphore synchronization protects a shared resource.")
    assert assistant.search("Semaphore", method="bm25")
    assert assistant.search("xylophone", method="bm25") == []


def test_duplicate_content_and_raw_roundtrip(assistant):
    raw = b"# Process\nA process owns resources.\n"
    first = assistant.import_document("a.txt", raw)
    second = assistant.import_document("renamed.txt", raw)
    assert first.duplicate is False and second.duplicate is True
    assert len(assistant.list_documents()) == 1
    info, stored = assistant.get_document(first.document.id)
    assert stored == raw and info.filename == "a.txt"


def test_text_chunks_have_source_lines_and_cover_content(assistant):
    lines = [f"Line {i:04d}: unique content token{i}." for i in range(1, 90)]
    imported = assistant.import_document("long.txt", ("\n".join(lines)).encode())
    chunks = assistant._chunks([imported.document.id])
    assert chunks and all(c.start_line is not None and c.end_line is not None for c in chunks)
    assert chunks[0].start_line == 1 and chunks[-1].end_line >= len(lines) - 1
    for i in range(1, 90):
        assert any(f"unique content token{i}." in c.text for c in chunks)
    for chunk in chunks:
        source_span = "\n".join(lines[chunk.start_line - 1:chunk.end_line])
        assert chunk.text in source_span


def _pdf(pages):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=300, height=300)
        if text is not None:
            font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                                         NameObject("/Subtype"): NameObject("/Type1"),
                                                         NameObject("/BaseFont"): NameObject("/Helvetica")}))
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject()
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(f"BT /F1 12 Tf 10 100 Td ({safe}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    out = BytesIO(); writer.write(out); return out.getvalue()


def test_pdf_page_numbers_and_low_text_page_warning(assistant):
    result = assistant.import_document("sample.pdf", _pdf([
        "This page contains enough meaningful extracted words for text indexing and page citation.",
        "7",
        "The third page again contains enough meaningful extracted content for the document search index.",
    ]))
    chunks = assistant._chunks([result.document.id])
    assert {chunk.page for chunk in chunks} == {1, 3}
    assert all(chunk.start_line is None for chunk in chunks)
    assert any("第 2 页" in warning and "少量文字" in warning for warning in result.warnings)


def test_pdf_encrypted_with_empty_password_can_be_imported(assistant):
    from pypdf import PdfReader, PdfWriter
    raw_pdf = _pdf(["This encrypted page contains enough words to pass the text extraction threshold for indexing."])
    writer = PdfWriter(clone_from=PdfReader(BytesIO(raw_pdf)))
    writer.encrypt(user_password="", owner_password="owner", algorithm="AES-128")
    encrypted = BytesIO(); writer.write(encrypted)
    imported = assistant.import_document("empty-password.pdf", encrypted.getvalue())
    chunks = assistant._chunks([imported.document.id])
    assert len(chunks) == 1 and chunks[0].page == 1


def test_pdf_invalid_surrogate_is_replaced_with_page_warning_and_raw_preserved(assistant, monkeypatch):
    from pypdf._page import PageObject
    raw = _pdf(["This page has placeholder content which is replaced by the extraction stub for regression testing."])
    monkeypatch.setattr(PageObject, "extract_text", lambda self, *args, **kwargs: "broken\udc80text " * 20)
    imported = assistant.import_document("surrogate.pdf", raw)
    info, stored = assistant.get_document(imported.document.id)
    chunks = assistant._chunks([info.id])
    assert stored == raw
    assert chunks and all("\udc80" not in chunk.text for chunk in chunks)
    assert all("\ufffd" in chunk.text for chunk in chunks)
    assert any("第 1 页" in warning and "替换" in warning for warning in imported.warnings)


def test_corrupt_and_scanned_pdf_are_rejected(assistant):
    with pytest.raises(ValueError):
        assistant.import_document("broken.pdf", b"not a pdf")
    scanned = _pdf([None])
    with pytest.raises(ValueError, match="没有可提取文字"):
        assistant.import_document("scan.pdf", scanned)


def test_delete_cascades_chunks_and_vectors(assistant):
    d = assistant.import_document("a.txt", b"deadlock process scheduling content")
    assert assistant.build_index() > 0
    assert assistant.delete_document(d.document.id)
    assert assistant._chunks() == []
    assert assistant.index_status()["total_chunks"] == assistant.index_status()["indexed_chunks"] == 0
    with pytest.raises(KeyError):
        assistant.get_document(d.document.id)
    assert assistant.delete_document(d.document.id) is False


def test_model_signature_mismatch_is_rejected(tmp_path):
    one = StudyAssistant(tmp_path, embedder=FakeEmbedder())
    one.import_document("a.txt", b"some useful content")
    one.build_index()
    class Other(FakeEmbedder): signature = "different-test-model"
    two = StudyAssistant(tmp_path, embedder=Other())
    with pytest.raises(RuntimeError, match="不同的嵌入模型"):
        two.build_index()


def test_invalid_citations_are_removed_and_warned(tmp_path):
    bad = FakeGenerator(answer="unsupported [9]", citations=[9])
    service = StudyAssistant(tmp_path, embedder=FakeEmbedder(), generator=bad)
    service.import_document("a.txt", b"deadlock and process")
    result = service.ask("deadlock", method="bm25")
    assert result.citations == [] and result.warnings


def test_generator_exception_and_malformed_payload_fall_back(tmp_path):
    for generator in (FakeGenerator(exc=RuntimeError("offline")), object()):
        service = StudyAssistant(tmp_path / str(id(generator)), embedder=FakeEmbedder(), generator=generator)
        service.import_document("a.txt", b"deadlock and process")
        result = service.ask("deadlock", method="bm25")
        assert result.hits and result.error and "原文片段" in result.answer


def test_ollama_malformed_json_is_reported(monkeypatch):
    import httpx
    from study_assistant.generation import OllamaGenerator

    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "not valid json"}}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="无效 JSON"):
        OllamaGenerator().generate("question", [])


def test_true_and_false_refusal_flags_are_preserved(tmp_path):
    for flag in (False, True):
        service = StudyAssistant(tmp_path / str(flag), embedder=FakeEmbedder(), generator=FakeGenerator(answer="根据材料。[1]", citations=[1], refused=flag))
        service.import_document("a.txt", b"deadlock and process")
        assert service.ask("deadlock", method="bm25").refused is flag


def test_fake_vectors_are_explicitly_test_only(fake_embedder):
    assert fake_embedder.signature.startswith("test-only-")


def test_partial_dense_index_is_rejected_after_new_document(assistant):
    assistant.import_document("first.txt", b"process and scheduling")
    assistant.build_index()
    assistant.import_document("second.txt", b"virtual memory and page table")
    with pytest.raises(RuntimeError):
        assistant.search("page table", method="dense")
    with pytest.raises(RuntimeError):
        assistant.search("page table", method="hybrid")


@pytest.mark.parametrize("payload", [
    {"answer": "claim [9]", "citations": [9], "refused": False},
    {"answer": "claim [1]", "citations": [], "refused": False},
    {"answer": "claim [2]", "citations": [1], "refused": False},
    {"answer": "claim [1]", "citations": [1], "refused": "false"},
    {"answer": "claim [1]", "citations": [True], "refused": False},
    {"answer": "claim [999]", "citations": [1], "refused": False},
    {"answer": "claim [1]", "citations": [2], "refused": False},
    {"answer": 12, "citations": [1], "refused": False},
])
def test_invalid_generator_payload_fails_closed_and_preserves_retrieval(tmp_path, payload):
    class Fixed:
        def generate(self, question, hits): return payload
    service = StudyAssistant(tmp_path, embedder=FakeEmbedder(), generator=Fixed())
    service.import_document("a.txt", b"deadlock and process")
    result = service.ask("deadlock", method="bm25")
    assert result.hits and result.error
    assert result.answer == "生成未完成，请查看下方检索到的原文片段。"
    assert result.citations == []


def test_unreferenced_answer_is_not_shown_as_valid(tmp_path):
    class Uncited:
        def generate(self, question, hits): return {"answer": "unsupported statement", "citations": [], "refused": False}
    service = StudyAssistant(tmp_path, embedder=FakeEmbedder(), generator=Uncited())
    service.import_document("a.txt", b"deadlock and process")
    result = service.ask("deadlock", method="bm25")
    assert result.error and result.citations == []


def test_no_hits_can_be_a_refusal(tmp_path):
    service = StudyAssistant(tmp_path, embedder=FakeEmbedder(), generator=FakeGenerator(refused=True))
    result = service.ask("anything", method="bm25")
    assert result.refused is True and result.hits == []
