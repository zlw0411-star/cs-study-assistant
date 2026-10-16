"""Integration review regressions: conservative evidence and bounded desktop UI."""
from dataclasses import replace
import os

import pytest

from study_assistant.analysis import _validate_outline
from study_assistant.materials import MaterialService


def test_body_substring_is_not_evidence_of_an_original_heading():
    node = {"title": "栈", "origin": "original", "summary": "表达式求值",
            "source_ids": ["S1"], "children": []}
    result = _validate_outline([node], {"S1"}, {"S1": "本节用栈实现表达式求值。"})
    assert result[0]["origin"] == "inferred"


@pytest.mark.parametrize("line", ["第二章 栈", "## 第二章 栈", "## 第二章  栈 ##"])
def test_whole_heading_lines_remain_original(line):
    node = {"title": "第二章 栈", "origin": "original", "summary": "表达式求值",
            "source_ids": ["S1"], "children": []}
    result = _validate_outline([node], {"S1"}, {"S1": line + "\n正文内容。"})
    assert result[0]["origin"] == "original"


@pytest.mark.parametrize("suffix", ["txt", "md"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_plain_text_form_feed_becomes_paragraph_boundary(tmp_path, suffix, encoding):
    service = MaterialService(tmp_path / "library")
    material = service.import_document(f"pages.{suffix}", "第一部分\f第二部分".encode(encoding)).material
    text = "\n".join(source.text for source in service.get_sources(material.id))
    assert text == "第一部分\n\n第二部分"


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QPlainTextEdit

from study_assistant.desktop_jobs import DesktopJobs, _safe_error
from test_desktop_jobs import final_json, map_json, model_config, wait_for_idle
from test_desktop_ui import FakeJobs, _report, _window, qt_app


@pytest.mark.parametrize("message", [
    "DOCX 文件包含重复的 XML 内容。",
    "DOCX XML 包含不安全的 DTD 声明。",
    "DOCX XML 层级过深，无法安全读取。",
    "文本文件包含二进制或损坏内容，请另存为 UTF-8 后重试。",
    "文本文件编码无法读取或内容损坏，请另存为 UTF-8 后重试。",
])
def test_new_fixed_import_errors_keep_actionable_desktop_messages(message):
    assert _safe_error("import", ValueError(message)) == message
    assert "private-key" not in _safe_error("import", ValueError("private-key"))


def test_client_cleanup_failure_does_not_discard_valid_report(tmp_path, qt_app):
    service = MaterialService(tmp_path / "library")
    material = service.import_document("notes.txt", b"Local notes.").material

    class CleanupFailureClient:
        config = model_config()

        def __init__(self):
            self.calls = 0

        def complete_json(self, system, user):
            self.calls += 1
            return map_json(["S1"]) if self.calls == 1 else final_json(["S1"])

        def close(self):
            raise OSError("private cleanup details")

    jobs = DesktopJobs(service, client_factory=lambda config: CleanupFailureClient())
    outcomes, failures = [], []
    jobs.succeeded.connect(lambda kind, result: outcomes.append((kind, result)))
    jobs.failed.connect(lambda kind, message: failures.append((kind, message)))
    assert jobs.start_analysis(material.id, model_config())
    wait_for_idle(qt_app, jobs)
    assert not failures
    assert len(outcomes) == 1 and outcomes[0][1]["report"]["title"] == "资料报告"
    assert service.get_analysis(material.id) is None  # only the UI persists reports


def test_many_missing_sources_do_not_expand_metadata_or_hide_details(tmp_path, qt_app, monkeypatch):
    service = MaterialService(tmp_path / "library")
    material = service.import_document("long.txt", ("资料正文。" * 550 + "\n\n").encode() * 160).material
    sources = service.get_sources(material.id)
    assert len(sources) == 160
    report = _report("长资料整理", sources[0].id)
    missing = [source.id for source in sources[1:]]
    report["metadata"].update(analyzed_source_count=160, total_source_count=160,
                              referenced_source_count=1, unreferenced_source_ids=missing)
    service.save_analysis(material.id, report)
    get_material = service.get_material
    warnings = [f"警告 {i}：" + "请核对原文。" * 50 for i in range(120)]
    monkeypatch.setattr(service, "get_material", lambda mid: replace(get_material(mid), warnings=warnings))
    window = _window(service, FakeJobs(service))
    try:
        window.setFont(QFont("Microsoft YaHei", 9))
        window.resize(900, 600)
        window.report_tabs.setCurrentIndex(1)
        qt_app.processEvents()
        assert len(window.report_meta_label.text()) < 180
        assert window.report_meta_label.height() <= 64
        assert window.report_views["outline"].height() >= 80
        assert window.report_details_button.isVisible()
        captured = []

        def capture(dialog):
            captured.extend(view.toPlainText() for view in dialog.findChildren(QPlainTextEdit))
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(QDialog, "exec", capture)
        window.report_details_button.click()
        assert captured
        assert set(missing) <= set(captured[-1].splitlines())
        assert "语义完整性" in captured[-1]
    finally:
        window.close()
        qt_app.processEvents()
