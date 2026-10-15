from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
import time

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QUrl, Signal, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QLineEdit

from study_assistant.desktop import MainWindow, ModelSettingsDialog
from study_assistant.analysis import OnlineAnalysisClient
from study_assistant.desktop_jobs import DesktopJobs
from study_assistant.material_types import MaterialImportResult
from study_assistant.materials import MaterialService


class FakeJobs(QObject):
    progress = Signal(int, int, str)
    succeeded = Signal(str, object)
    failed = Signal(str, str)
    cancelled = Signal(str)
    idle = Signal()

    def __init__(self, service):
        super().__init__()
        self.service = service
        self.busy = False
        self.job_kind = None
        self.cancel_calls = 0
        self.last_path = None
        self.last_material_id = None
        self.last_config = None

    def start_import(self, path):
        if self.busy:
            return False
        self.busy = True
        self.job_kind = "import"
        self.last_path = path
        return True

    def start_analysis(self, material_id, config):
        if self.busy:
            return False
        self.busy = True
        self.job_kind = "analysis"
        self.last_material_id = material_id
        self.last_config = config
        return True

    def cancel(self):
        self.cancel_calls += 1

    def complete_success(self, kind, payload):
        self.busy = False
        self.job_kind = None
        self.succeeded.emit(kind, payload)
        self.idle.emit()

    def complete_failure(self, kind, message="模拟错误"):
        self.busy = False
        self.job_kind = None
        self.failed.emit(kind, message)
        self.idle.emit()

    def complete_cancel(self, kind="analysis"):
        self.busy = False
        self.job_kind = None
        self.cancelled.emit(kind)
        self.idle.emit()


@pytest.fixture
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _report(title: str, sid: str, *, summary: str = "A source-backed summary", warnings=None):
    return {
        "title": title,
        "material_type": "讲义/文档",
        "summary": summary,
        "outline": [{"title": "Section", "summary": "Main idea", "source_ids": [sid], "children": []}],
        "knowledge_points": [{"name": "Concept", "explanation": "Explanation", "source_ids": [sid]}],
        "learning_suggestions": ["Review the source"],
        "warnings": warnings or [],
        "metadata": {"model": "fake-model", "generated_at": "2026-10-15T00:00:00Z", "analyzed_source_count": 1, "total_source_count": 1},
    }


def _seed(service, filename="lesson.txt", text="first source text", report_title="Saved report"):
    material = service.import_document(filename, text.encode()).material
    source = service.get_sources(material.id)[0]
    report = _report(report_title, source.id, warnings=["Review the original wording"])
    service.save_analysis(material.id, report)
    return material, source, report


def _window(service, jobs):
    window = MainWindow(service=service, jobs=jobs)
    window.show()
    QApplication.processEvents()
    return window


def test_desktop_library_source_navigation_and_real_exports(tmp_path, qt_app):
    service = MaterialService(tmp_path / "db")
    first, first_source, first_report = _seed(service, "first.txt", "first text")
    second = service.import_document("second.md", "second source text".encode()).material
    second_source = service.get_sources(second.id)[0]
    service.save_analysis(second.id, _report("Second report", second_source.id))
    jobs = FakeJobs(service)
    window = _window(service, jobs)
    assert window.library_list.count() == 2

    # Pick a known material, then follow an in-report source link to its preview.
    for row in range(window.library_list.count()):
        if window.library_list.item(row).data(Qt.ItemDataRole.UserRole) == first.id:
            window.library_list.setCurrentRow(row)
            break
    QApplication.processEvents()
    assert window.report_views["summary"].toPlainText().find("Saved report") >= 0
    window.report_views["outline"].anchorClicked.emit(QUrl(f"source:{first_source.id}"))
    assert window.source_list.currentItem().data(Qt.ItemDataRole.UserRole) == first_source.id
    assert window.source_preview.toPlainText() == first_source.text

    md_path = tmp_path / "report.md"
    json_path = tmp_path / "report.json"
    window.export_report(md_path)
    window.export_report(json_path)
    markdown = md_path.read_text(encoding="utf-8")
    exported = json.loads(json_path.read_text(encoding="utf-8"))
    assert "first.txt" in markdown and "AI 生成内容" in markdown
    assert first_source.id in markdown and first_source.text in markdown
    assert "Review the original wording" in markdown
    assert exported["source_index"][0]["locator"] == first_source.locator
    assert exported["source_index"][0]["text"] == first_source.text
    assert exported["report"]["metadata"]["model"] == "fake-model"

    # Switching files loads the independent report and source collection.
    for row in range(window.library_list.count()):
        if window.library_list.item(row).data(Qt.ItemDataRole.UserRole) == second.id:
            window.library_list.setCurrentRow(row)
            break
    QApplication.processEvents()
    assert "Second report" in window.report_views["summary"].toPlainText()
    assert window.source_preview.toPlainText() == second_source.text
    window.close()


def test_model_settings_are_password_masked_and_session_only(tmp_path, qt_app, monkeypatch):
    monkeypatch.setenv("CSA_ANALYSIS_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("CSA_ANALYSIS_MODEL", "model-from-env")
    monkeypatch.setenv("CSA_ANALYSIS_API_KEY", "secret-from-env")
    service = MaterialService(tmp_path / "db")
    _seed(service)
    jobs = FakeJobs(service)
    window = _window(service, jobs)
    dialog = ModelSettingsDialog(window._base_url, window._model, window._api_key, window)
    assert dialog.base_url_edit.text() == "https://model.example/v1"
    assert dialog.model_edit.text() == "model-from-env"
    assert dialog.api_key_edit.echoMode() == QLineEdit.EchoMode.Password
    dialog.api_key_edit.setText("changed-session-secret")
    assert dialog.values()[2] == "changed-session-secret"
    assert not (service.data_dir / "settings.json").exists()
    dialog.close()

    def accept_settings(self):
        self.base_url_edit.setText("https://session.example/v1")
        self.model_edit.setText("session-model")
        self.api_key_edit.setText("session-secret")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ModelSettingsDialog, "exec", accept_settings)
    window.settings_action.trigger()
    assert window._base_url == "https://session.example/v1"
    assert window._model == "session-model"
    assert window._api_key == "session-secret"

    # Settings must be complete before an explicit Analyze click can start a job.
    window._base_url = window._model = window._api_key = ""
    notices = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: notices.append(args))
    QTest.mouseClick(window.analyze_button, Qt.MouseButton.LeftButton)
    assert notices and jobs.last_config is None
    window.close()


def test_ui_file_picker_starts_async_import_and_selects_finished_material(tmp_path, qt_app, monkeypatch):
    service = MaterialService(tmp_path / "db")
    old, _, _ = _seed(service, "existing.txt", "old source")
    jobs = FakeJobs(service)
    window = _window(service, jobs)
    new_path = tmp_path / "new.md"
    new_path.write_text("new document", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(new_path), ""))
    QTest.mouseClick(window.import_button, Qt.MouseButton.LeftButton)
    assert jobs.last_path == str(new_path)
    assert jobs.busy
    assert not window.library_list.isEnabled()
    imported = service.import_document(new_path.name, new_path.read_bytes())
    jobs.complete_success("import", imported)
    assert window._material_id == imported.material.id
    assert window.source_preview.toPlainText() == "new document"
    assert window._material_id != old.id
    window.close()


def test_analysis_failure_keeps_previous_report(tmp_path, qt_app):
    service = MaterialService(tmp_path / "db")
    material, _, old_report = _seed(service)
    jobs = FakeJobs(service)
    window = _window(service, jobs)
    window._base_url, window._model, window._api_key = "https://example.invalid/v1", "fake-model", "fake-secret"
    QTest.mouseClick(window.analyze_button, Qt.MouseButton.LeftButton)
    assert jobs.busy and jobs.last_material_id == material.id
    jobs.complete_failure("analysis", "安全的服务错误")
    assert service.get_analysis(material.id) == old_report
    assert "安全的服务错误" in window.status_label.text()
    assert "fake-secret" not in window.status_label.text()
    window.close()


def test_report_save_database_failure_keeps_old_report(tmp_path, qt_app, monkeypatch):
    service = MaterialService(tmp_path / "db")
    material, source, old_report = _seed(service, report_title="Previous")
    jobs = FakeJobs(service)
    window = _window(service, jobs)
    original_save = service.save_analysis
    def fail_save(material_id, report):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(service, "save_analysis", fail_save)
    jobs.complete_success("analysis", {"material_id": material.id, "report": _report("New", source.id)})
    assert service.get_analysis(material.id) == old_report
    assert "报告保存失败" in window.status_label.text()
    monkeypatch.setattr(service, "save_analysis", original_save)
    window.close()


def test_export_appends_extension_and_preserves_existing_file_on_write_error(tmp_path, qt_app, monkeypatch):
    service = MaterialService(tmp_path / "db")
    _seed(service)
    jobs = FakeJobs(service)
    window = _window(service, jobs)
    bare_path = tmp_path / "report"
    window.export_report(bare_path, "md")
    target = tmp_path / "report.md"
    assert target.exists()
    assert "已导出" in window.status_label.text()

    target.write_text("previous export", encoding="utf-8")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    def fail_replace(self, destination):
        raise OSError("simulated write failure")
    monkeypatch.setattr(Path, "replace", fail_replace)
    window.export_report(target, "md")
    assert warnings
    assert target.read_text(encoding="utf-8") == "previous export"
    assert "导出失败" in window.status_label.text()
    assert not list(tmp_path.glob(".report.md.*.tmp"))
    window.close()


def test_busy_close_cancels_without_late_report_write(tmp_path, qt_app):
    service = MaterialService(tmp_path / "db")
    material, source, old_report = _seed(service, report_title="Before close")
    jobs = FakeJobs(service)
    window = _window(service, jobs)
    window._base_url, window._model, window._api_key = "https://example.invalid/v1", "fake-model", "fake-secret"
    QTest.mouseClick(window.analyze_button, Qt.MouseButton.LeftButton)
    assert jobs.busy

    window.close()
    QApplication.processEvents()
    assert window.isVisible()
    assert window._closing
    assert jobs.cancel_calls == 1
    assert not window.import_button.isEnabled()
    assert not window.delete_button.isEnabled()
    assert not window.settings_action.isEnabled()
    assert not window.cancel_button.isEnabled()
    window.close()
    assert jobs.cancel_calls == 1

    # A terminal success arriving during shutdown is ignored and cannot save late data.
    jobs.complete_success("analysis", {"material_id": material.id, "report": _report("Late report", source.id)})
    QApplication.processEvents()
    QTest.qWait(20)
    assert service.get_analysis(material.id) == old_report
    assert not window.isVisible()


def test_real_desktop_jobs_and_window_save_mocked_online_analysis(tmp_path, qt_app):
    service = MaterialService(tmp_path / "db")
    material = service.import_document("integration.txt", b"Distributed systems use replication for availability.").material
    seen = []

    def handler(request):
        seen.append(request)
        payload = json.loads(request.content)
        prompt = payload["messages"][1]["content"]
        if "请为资料" in prompt:
            model_result = {
                "title": "Replication notes",
                "material_type": "讲义/文档",
                "summary": "Replication can improve availability.",
                "outline": [{"title": "Replication", "summary": "Copies data across nodes.", "source_ids": ["S1"], "children": []}],
                "knowledge_points": [{"name": "Availability", "explanation": "Replicas can serve requests during failures.", "source_ids": ["S1"]}],
                "learning_suggestions": ["Compare synchronous and asynchronous replication."],
                "warnings": [],
            }
        else:
            model_result = {
                "coverage_source_ids": ["S1"],
                "summary": "The text discusses replication.",
                "outline": [{"title": "Replication", "summary": "Data copies.", "source_ids": ["S1"], "children": []}],
                "knowledge_points": [{"name": "Replication", "explanation": "Data copies improve availability.", "source_ids": ["S1"]}],
            }
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(model_result)}}]})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    jobs = DesktopJobs(service, client_factory=lambda config: OnlineAnalysisClient(config, client=http_client))
    window = _window(service, jobs)
    window._base_url = "https://mock.example/v1"
    window._model = "desktop-test-model"
    window._api_key = "desktop-test-secret"
    QTest.mouseClick(window.analyze_button, Qt.MouseButton.LeftButton)
    assert jobs.busy

    deadline = time.monotonic() + 5
    while jobs.busy and time.monotonic() < deadline:
        qt_app.processEvents()
        QTest.qWait(10)
    for _ in range(20):
        qt_app.processEvents()
        QTest.qWait(5)

    saved = service.get_analysis(material.id)
    assert saved is not None
    assert saved["title"] == "Replication notes"
    assert saved["metadata"]["model"] == "desktop-test-model"
    assert len(seen) == 2
    assert all(request.headers["Authorization"] == "Bearer desktop-test-secret" for request in seen)
    assert "分析完成" in window.status_label.text()
    http_client.close()
    window.close()
