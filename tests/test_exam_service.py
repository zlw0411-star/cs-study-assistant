from io import BytesIO
import json
import re
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from study_assistant import ExamService

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "examples" / "knowledge_points.json"


def pdf_pages(pages):
    # The PDF text is supplied by a tiny test-only PdfReader stub below. This
    # keeps Chinese fixtures exact without embedding a font or shipping PDFs.
    payload = json.dumps(pages, ensure_ascii=False).encode("utf-8").hex()
    return b"%PDF-1.4\n%CSA_TEST_TEXT=" + payload.encode("ascii") + b"\n%%EOF"


def renderable_pdf(text):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 10 100 Td ({safe}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO(); writer.write(output); return output.getvalue()


@pytest.fixture(autouse=True)
def mock_text_pdf_reader(monkeypatch):
    import pypdf

    real_reader = pypdf.PdfReader

    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class Reader:
        def __init__(self, stream, strict=False):
            raw = stream.getvalue()
            marker = b"%CSA_TEST_TEXT="
            if marker not in raw:
                self._real = real_reader(stream, strict=strict)
                self.pages = self._real.pages
                self.is_encrypted = self._real.is_encrypted
                return
            self.is_encrypted = False
            data = raw.split(marker, 1)[1].splitlines()[0]
            self.pages = [Page("\n".join(lines)) for lines in json.loads(bytes.fromhex(data.decode()).decode("utf-8"))]

    monkeypatch.setattr(pypdf, "PdfReader", Reader)


@pytest.fixture
def exams(tmp_path):
    return ExamService(tmp_path / "exam-db")


def test_catalog_has_stable_schema_and_transfer_learning_for_all_topics():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    topics = catalog["topics"]
    ids = {topic["id"] for topic in topics}
    assert len(topics) >= 35 and len(ids) == len(topics)
    assert {t["subject"] for t in topics} == {"数据结构", "计算机组成原理", "操作系统", "计算机网络"}
    for topic in topics:
        assert all(key in topic for key in ("summary", "prerequisites", "checklist", "common_mistakes", "keywords", "order", "why_questions", "transfer_prompt", "transfer_hint"))
        assert 2 <= len(topic["checklist"]) <= 3
        assert 1 <= len(topic["common_mistakes"]) <= 2
        assert 1 <= len(topic["why_questions"]) <= 2
        assert topic["transfer_prompt"].strip() and topic["transfer_hint"].strip()
        assert set(topic["prerequisites"]) <= ids
    by_id = {topic["id"]: topic for topic in topics}
    assert {"ds_huffman", "ds_btree", "ds_hash", "ds_block_search", "ds_aoe_critical_path", "ds_dynamic_programming", "co_ieee754", "co_byte_order_alignment", "co_integer_conversion", "co_bus", "os_virtualization", "cn_circuit_switching", "cn_hamming_code", "cn_physical_signaling", "cn_medium_access"} <= ids
    assert by_id["ds_huffman"]["subject"] == "数据结构"


def test_all_public_mock_exam_files_are_explicitly_synthetic():
    examples = ROOT / "examples" / "mock_exams"
    files = sorted(examples.glob("*.txt"))
    assert len(files) == 8
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "自编模拟题" in text
        assert "非官方考试资料" in text


def test_empty_service_and_topic_reflection_is_separate_from_question_progress(exams):
    assert exams.list_papers() == []
    assert exams.list_questions() == []
    stats = {row["topic_id"]: row for row in exams.topic_stats()}
    row = stats["os_process_thread"]
    assert row["total"] == row["wrong"] == row["self_checked"] == 0
    assert row["has_reflection"] is False
    assert exams.get_topic_progress("os_process_thread")["self_checked"] is False
    with pytest.raises(KeyError):
        exams.get_topic_progress("no-such-topic")


def test_import_duplicate_preserves_original_bytes_and_question_metadata(exams):
    raw = pdf_pages([["全国硕士研究生招生考试 2025", "一、选择题 1. 进程与线程的区别是什么？请结合PCB分析资源和执行状态。"]])
    result = exams.import_pdf("2025.pdf", raw, year=2025)
    duplicate = exams.import_pdf("renamed.pdf", raw, year=2024)
    assert result.duplicate is False and duplicate.duplicate is True
    assert duplicate.paper.id == result.paper.id
    info, stored = exams.get_paper_document(result.paper.id)
    assert stored == raw and info.question_count == 1 and info.year == 2025
    q = exams.list_questions(paper_id=info.id)[0]
    assert q.number == 1 and q.subject == "操作系统" and q.start_page == q.end_page == 1


def test_cross_page_question_span_and_repeated_exam_headers_are_removed(exams):
    raw = pdf_pages([
        ["计算机学科专业基础 试题 第1页", "1. 进程通过PCB描述运行状态，线程共享进程资源，下面继续说明。"],
        ["计算机学科专业基础 试题 第2页", "线程切换仍需保存执行现场，请结合调度和资源隔离分析其差别。", "— 2 —"],
    ])
    result = exams.import_pdf("2025-header.pdf", raw)
    q = exams.list_questions(paper_id=result.paper.id)[0]
    assert (q.start_page, q.end_page) == (1, 2)
    assert "第1页" not in q.text and "第2页" not in q.text
    assert "线程切换仍需保存执行现场" in q.text


def test_subquestion_and_code_numbers_do_not_split_main_question(exams):
    raw = pdf_pages([["1. 设程序如下，分析时间复杂度 O(n", "2", ")。代码中循环变量 i 从 1 到 10。", "（1）说明算法过程。", "（2）分析额外空间。", "int main() { return 0; }", "结合输入规模和循环嵌套层数解释每一步推导依据。"]])
    result = exams.import_pdf("numbers.pdf", raw)
    questions = exams.list_questions(paper_id=result.paper.id)
    assert [q.number for q in questions] == [1]
    assert "（1）" in questions[0].text and "（2）" in questions[0].text
    assert "int main()" in questions[0].text
    assert re.search(r"O\(n\s*2\s*\)", questions[0].text)


def test_missing_question_one_keeps_questions_two_and_three_with_warning(exams):
    raw = pdf_pages([["2. 说明进程调度的目标并解释响应时间，同时讨论公平性与吞吐量。", "3. 给出一种调度变式并说明其适用条件，比较它在不同负载下的表现。"]])
    result = exams.import_pdf("gap.pdf", raw)
    assert [q.number for q in exams.list_questions(paper_id=result.paper.id)] == [2, 3]
    assert any("未识别到全部主问题号" in warning and "1" in warning for warning in result.warnings)


def test_trailing_scanned_page_does_not_extend_last_question_and_cross_page_question_keeps_span(exams):
    raw = pdf_pages([
        ["39. 说明给定操作系统管理策略的主要步骤，并解释其设计目标和适用场景，再讨论该方案的关键条件。"],
        ["续：分析策略在不同资源约束下的影响，并给出判断过程和比较依据。", "40. 结合前述系统说明另一种资源管理办法的特点和限制，并说明适用边界。"],
        ["29"],
    ])
    result = exams.import_pdf("2026-408-recall.pdf", raw, year=2026, source_kind="recall")
    by_number = {q.number: q for q in exams.list_questions(paper_id=result.paper.id)}
    assert by_number[39].start_page == 1 and by_number[39].end_page == 2
    assert by_number[40].start_page == 2 and by_number[40].end_page == 2
    assert any("第 3 页" in warning for warning in result.warnings)


def test_verified_formal_layout_prior_only_applies_to_complete_known_408_sets(exams):
    from study_assistant.exams import ExamService

    assert ExamService._verified_layout_subject("2025-408-original.pdf", 2025, "paper", 1, True) == "数据结构"
    assert ExamService._verified_layout_subject("2025-408-original.pdf", 2025, "paper", 12, True) == "计算机组成原理"
    assert ExamService._verified_layout_subject("2025-408-original.pdf", 2025, "paper", 23, True) == "操作系统"
    assert ExamService._verified_layout_subject("2025-408-original.pdf", 2025, "paper", 33, True) == "计算机网络"
    assert ExamService._verified_layout_subject("2025-408-original.pdf", 2025, "paper", 12, False) is None
    assert ExamService._verified_layout_subject("2026-408-recall.pdf", 2026, "recall", 12, True) is None
    assert ExamService._verified_layout_subject("mock.pdf", 2025, "paper", 12, True) is None


def test_complete_verified_408_number_layout_applies_coarse_subject_only(exams):
    lines = [
        f"{n}. 该测试样题描述一项计算机系统任务，要求根据给定条件分析执行结果并说明推导过程。"
        for n in range(1, 48)
    ]
    paper = exams.import_pdf("2025-408-original.pdf", pdf_pages([lines]), year=2025).paper
    by_number = {q.number: q for q in exams.list_questions(paper_id=paper.id)}
    assert len(by_number) == 47
    assert by_number[1].subject == "数据结构"
    assert by_number[12].subject == "计算机组成原理"
    assert by_number[23].subject == "操作系统"
    assert by_number[33].subject == "计算机网络"
    assert by_number[41].subject == "数据结构"
    assert by_number[43].subject == "计算机组成原理"
    assert by_number[45].subject == "操作系统"
    assert by_number[47].subject == "计算机网络"


def test_reclassify_automatic_preserves_manual_classification_and_learning_progress(exams):
    raw = pdf_pages([[
        "1. 进程线程 semaphore 死锁 等技术概念均出现，下面要求结合条件分析和内存分页系统关系。",
        "2. 该系统根据给定的一组资源和任务执行规则检查运行状态，请解释判断过程以及采用此规则的条件。",
    ]])
    paper = exams.import_pdf("mock.pdf", raw, year=2025).paper
    question = exams.list_questions(paper_id=paper.id)[0]
    exams.save_progress(question.id, status="review", is_wrong=True, notes="保留我的学习记录")
    manual = exams.set_classification(question.id, "操作系统", ["os_deadlock"])
    reflection = exams.save_topic_progress("os_deadlock", "必要条件", "改变资源可剥夺条件后检查死锁必要性", True)
    assert exams.reclassify_automatic() >= 1
    current = exams.get_question(question.id)
    assert current.classification_manual and current.topic_ids == ["os_deadlock"]
    assert current.status == "review" and current.is_wrong and current.notes == "保留我的学习记录"
    assert exams.get_topic_progress("os_deadlock")["principle_note"] == reflection["principle_note"]
    automatic = exams.list_questions(paper_id=paper.id)[1]
    exams.save_progress(automatic.id, status="mastered", is_wrong=True, notes="自动分类题目的记录")
    assert exams.reclassify_automatic() == 1
    refreshed = exams.get_question(automatic.id)
    assert refreshed.status == "mastered" and refreshed.is_wrong and refreshed.notes == "自动分类题目的记录"


def test_keyword_classifier_avoids_ip_mips_and_pv_ipv6_substrings(exams):
    ip = exams._classify("MIPS流水线中控制冲突的处理方法，请讨论指令执行阶段与寄存器旁路。")
    pv = exams._classify("IPv6首部中的PV字段在该设备定义中代表版本参数，请解释其编码。")
    assert ip[0] == "计算机组成原理"
    assert ip[1] != ["cn_ip_subnet"]
    assert pv[1] != ["cn_ip_subnet"]


def test_close_same_subject_topic_competition_requests_review_but_keeps_winner(exams, monkeypatch):
    catalog = [
        {"id": "ds_winner", "subject": "数据结构", "keywords": ["进程线程池"]},
        {"id": "ds_runner_up", "subject": "数据结构", "keywords": ["进程线程"]},
    ]
    monkeypatch.setattr(exams, "_load_catalog", lambda: catalog)
    subject, topic_ids, _, needs_review = exams._classify("进程线程池")
    assert subject == "数据结构"
    assert topic_ids == ["ds_winner"]
    assert needs_review is True

    monkeypatch.setattr(exams, "_load_catalog", lambda: catalog[:1])
    subject, topic_ids, _, needs_review = exams._classify("进程线程池")
    assert subject == "数据结构"
    assert topic_ids == ["ds_winner"]
    assert needs_review is False


def test_progress_filters_manual_classification_and_persistence(exams, tmp_path):
    raw = pdf_pages([["1. 信号量P操作与V操作必须原子执行，请分析生产者消费者缓冲区同步次序，并说明各计数值代表的可用资源。"]])
    paper = exams.import_pdf("sync.pdf", raw, year=2020).paper
    q = exams.list_questions(paper_id=paper.id)[0]
    saved = exams.save_progress(q.id, status="review", is_wrong=True, notes="重新核对P/V次序")
    assert saved.status == "review" and saved.is_wrong and saved.notes
    assert exams.list_questions(wrong_only=True) == [saved]
    assert exams.list_questions(status="review", year=2020, topic_id="os_synchronization") == [saved]
    with pytest.raises(TypeError):
        exams.save_topic_progress("os_synchronization", "原理说明", "变式推导", 1)
    topic = exams.save_topic_progress("os_synchronization", "P/V原子操作及互斥约束", "容量变为3时空槽初值改为3", True)
    assert bool(topic["self_checked"]) is True
    assert topic["principle_note"] and topic["transfer_note"]
    other = ExamService(tmp_path / "exam-db")
    assert other.get_question(q.id).is_wrong is True
    assert bool(other.get_topic_progress("os_synchronization")["self_checked"]) is True
    stat = next(x for x in other.topic_stats() if x["topic_id"] == "os_synchronization")
    assert stat["self_checked"] == 1 and stat["has_reflection"] is True
    classified = other.set_classification(q.id, "操作系统", ["os_synchronization", "os_deadlock"])
    assert classified.classification_manual and not classified.needs_review
    assert set(classified.topic_ids) == {"os_synchronization", "os_deadlock"}
    with pytest.raises(ValueError):
        other.set_classification(q.id, "操作系统", ["cn_ip_subnet"])


def test_topic_self_check_requires_both_notes_and_does_not_imply_question_mastery(exams):
    raw = pdf_pages([["1. 分页虚拟内存通过页表映射虚拟页和物理页框，请说明地址转换过程，并分析缺页异常时操作系统如何恢复执行。"]])
    paper = exams.import_pdf("paging.pdf", raw).paper
    question = exams.list_questions(paper_id=paper.id)[0]
    with pytest.raises(ValueError):
        exams.save_topic_progress("os_paging_virtual_memory", "只解释原理", "", True)
    saved = exams.save_topic_progress("os_paging_virtual_memory", "页号查表，偏移保留", "扩大页大小后页内偏移位增加", True)
    assert exams.get_question(question.id).status == "new"
    assert bool(saved["self_checked"]) is True


def test_scanned_answer_attach_preview_and_cascade_delete(exams):
    paper_raw = renderable_pdf("1. Process state transition analysis question for operating systems.")
    answer_raw = renderable_pdf("Scanned answer material placeholder")
    paper = exams.import_pdf("paper.pdf", paper_raw).paper
    warnings = exams.attach_answer_pdf(paper.id, "answer.pdf", answer_raw)
    assert warnings == []
    info, stored = exams.get_paper_document(paper.id, answer=True)
    assert info.answer_filename == "answer.pdf" and stored == answer_raw
    assert exams.render_page(paper.id, 1, answer=True).startswith(b"\x89PNG\r\n\x1a\n")
    assert exams.render_page(paper.id, 1, answer=False).startswith(b"\x89PNG\r\n\x1a\n")
    qid = exams.list_questions(paper_id=paper.id)[0].id
    assert exams.delete_paper(paper.id) is True
    assert exams.list_questions(paper_id=paper.id) == []
    with pytest.raises(KeyError):
        exams.get_question(qid)
    assert exams.delete_paper(paper.id) is False
