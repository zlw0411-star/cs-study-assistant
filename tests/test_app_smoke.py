from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from study_assistant import ExamService


def _pdf_with_ascii_question(suffix=""):
    writer = PdfWriter()
    page = writer.add_blank_page(width=420, height=595)
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    text = "1. Explain semaphore synchronization and producer consumer buffer ordering with atomic P and V operations. " + suffix
    stream.set_data(f"BT /F1 12 Tf 20 500 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    result = BytesIO(); writer.write(result); return result.getvalue()


def test_streamlit_exam_app_opens_with_empty_temporary_database(tmp_path, monkeypatch):
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("CSA_DATA_DIR", str(tmp_path / "app-data"))
    app = streamlit_testing.AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15
    ).run()
    assert not app.exception
    assert any("当前题库" in item.value for item in app.caption)
    assert any("题目列表" in item.value for item in app.subheader)


def test_seeded_question_page_renders_learning_and_topic_reflection_controls(tmp_path, monkeypatch):
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    data_dir = tmp_path / "seeded"
    service = ExamService(data_dir)
    older = service.import_pdf("2020-demo.pdf", _pdf_with_ascii_question("older variant"), year=2020).paper
    newer = service.import_pdf("2021-demo.pdf", _pdf_with_ascii_question("newer variant"), year=2021).paper
    older_question = service.list_questions(paper_id=older.id)[0]
    newer_question = service.list_questions(paper_id=newer.id)[0]
    service.set_classification(older_question.id, "操作系统", ["os_synchronization"])
    service.set_classification(newer_question.id, "操作系统", ["os_synchronization"])
    service.attach_answer_pdf(newer.id, "synthetic-answer.pdf", _pdf_with_ascii_question("synthetic answer material"))

    monkeypatch.setenv("CSA_DATA_DIR", str(data_dir))
    app = streamlit_testing.AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15
    ).run()
    assert not app.exception
    assert any("本题复习状态（自评）" in item.label for item in app.selectbox)
    assert any("保存学习记录" == item.label for item in app.button)
    assert not any(item.key == "topic_principle_os_synchronization" for item in app.text_area)
    app.button(key="start_topic_learning").click().run()
    assert any("解释为什么" in item.value for item in app.markdown)
    assert any("变式问题" in item.value for item in app.markdown)
    assert any("topic_principle_os_synchronization" == item.key for item in app.text_area)

    # Question-level self-rating and topic-level reflection persist separately.
    app.selectbox(key="progress_status").select("需复习")
    app.checkbox(key="progress_wrong").check()
    app.text_area(key="progress_notes").set_value("遗漏了互斥信号量的释放步骤")
    app.run()
    app.button(key="save_progress").click().run()
    saved_question = service.get_question(newer_question.id)
    assert saved_question.status == "review" and saved_question.is_wrong
    assert saved_question.notes == "遗漏了互斥信号量的释放步骤"

    app.text_area(key="topic_principle_os_synchronization").set_value("P/V操作原子执行；mutex保护临界区。")
    app.text_area(key="topic_transfer_os_synchronization").set_value("容量变为3时empty初值为3，逐项核对P/V顺序。")
    app.checkbox(key="topic_self_checked_os_synchronization").check()
    app.button(key="save_topic_progress_os_synchronization").click().run()
    reflection = service.get_topic_progress("os_synchronization")
    assert reflection["principle_note"].startswith("P/V操作")
    assert reflection["transfer_note"].startswith("容量变为3")
    assert bool(reflection["self_checked"])
    assert service.get_question(newer_question.id).status == "review"

    app.button(key="toggle_answer").click().run()
    related_button_key = f"practice_related_os_synchronization_{older_question.id}"
    assert any(item.key == related_button_key for item in app.button)
    app.button(key=related_button_key).click().run()
    assert not app.exception
    assert app.selectbox(key="year_filter").value == "2020"
    assert app.selectbox(key="selected_question").value == older_question.id
    assert app.session_state["show_answer_material"] is False
    assert not any(item.key == "toggle_answer" for item in app.button)
    assert not any(item.key == "topic_principle_os_synchronization" for item in app.text_area)
    app.button(key="start_topic_learning").click().run()
    assert app.text_area(key="topic_principle_os_synchronization").value == reflection["principle_note"]
    assert app.text_area(key="topic_transfer_os_synchronization").value == reflection["transfer_note"]
    app.selectbox(key="year_filter").select("全部年份").run()
    app.selectbox(key="selected_question").select(newer_question.id).run()
    assert app.selectbox(key="progress_status").value == "需复习"
    assert app.checkbox(key="progress_wrong").value is True
    assert app.text_area(key="progress_notes").value == "遗漏了互斥信号量的释放步骤"
    assert bool(service.get_topic_progress("os_synchronization")["self_checked"])


def test_unclassified_subject_sidebar_stats_exclude_other_subjects(tmp_path, monkeypatch):
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    data_dir = tmp_path / "unclassified-filter"
    service = ExamService(data_dir)
    unclassified_paper = service.import_pdf(
        "unclassified.pdf", _pdf_with_ascii_question("unclassified synthetic question"), year=2024
    ).paper
    unclassified_question = service.list_questions(paper_id=unclassified_paper.id)[0]
    service.set_classification(unclassified_question.id, "待分类", [])

    os_paper = service.import_pdf(
        "os.pdf", _pdf_with_ascii_question("OS synthetic question"), year=2023
    ).paper
    os_question = service.list_questions(paper_id=os_paper.id)[0]
    service.set_classification(os_question.id, "操作系统", ["os_synchronization"])
    service.save_progress(os_question.id, status="review", is_wrong=True)

    monkeypatch.setenv("CSA_DATA_DIR", str(data_dir))
    app = streamlit_testing.AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15
    ).run()
    assert not app.exception

    app.selectbox(key="subject_filter").select("待分类").run()
    assert any("当前筛选到 1 题" == item.value for item in app.caption)
    assert not any("未解决错题" in item.value for item in app.caption)

    app.selectbox(key="subject_filter").select("操作系统").run()
    assert any("当前筛选到 1 题" == item.value for item in app.caption)
    assert any("未解决错题 1" in item.value for item in app.caption)
