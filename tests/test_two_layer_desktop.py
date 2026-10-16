"""Two-layer desktop regressions; all persisted state stays under tmp_path."""
from __future__ import annotations

import copy
import json
import os
import socket
import sqlite3
import time

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QMessageBox

from study_assistant.desktop import MainWindow
from study_assistant.desktop_jobs import DesktopJobs
from study_assistant.materials import MaterialService


class TwoLayerJobs(QObject):
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
        self.analysis_calls = []

    def start_analysis(self, material_id, config):
        if self.busy:
            return False
        self.analysis_calls.append((material_id, config))
        self.busy = True
        self.job_kind = "analysis"
        return True

    def cancel(self):
        self.cancel_calls += 1

    def finish(self, outcome, payload=None):
        self.busy = False
        self.job_kind = None
        if outcome == "succeeded":
            self.succeeded.emit("analysis", payload)
        elif outcome == "failed":
            self.failed.emit("analysis", "模拟在线失败")
        else:
            self.cancelled.emit("analysis")
        self.idle.emit()


@pytest.fixture
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture(autouse=True)
def offline_session(monkeypatch):
    for key in ("CSA_ANALYSIS_BASE_URL", "CSA_ANALYSIS_MODEL", "CSA_ANALYSIS_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("two-layer desktop tests must not access the network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    # Avoid a modal dialog blocking an offscreen test; assertions inspect outcomes.
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Ok)


@pytest.fixture
def windows(qt_app):
    opened = []

    def create(service, jobs=None):
        window = MainWindow(service=service, jobs=jobs if jobs is not None else TwoLayerJobs(service))
        opened.append(window)
        window.resize(900, 600)
        window.show()
        qt_app.processEvents()
        return window

    yield create
    for window in reversed(opened):
        if window.jobs.busy:
            window.jobs.cancel()
            if isinstance(window.jobs, TwoLayerJobs):
                window.jobs.finish("cancelled")
            else:
                _wait_idle(window.jobs, qt_app)
        window.close()
    qt_app.processEvents()


def _import(service, filename="local.md", marker="LOCAL SOURCE"):
    text = (f"# {marker}\n\n" + f"{marker}: first definition.\n" * 70
            + "\n## Details\n\n" + f"{marker}: second explanation.\n" * 70)
    return service.import_document(filename, text.encode("utf-8")).material


def _online_report(service, material, title="ONLINE ONLY"):
    sid = service.get_sources(material.id)[-1].id
    return {
        "title": title,
        "material_type": "讲义/文档",
        "summary": f"{title} summary",
        "outline": [{"title": title, "summary": "Online outline", "source_ids": [sid], "children": []}],
        "knowledge_points": [{"name": title, "explanation": "Online explanation", "source_ids": [sid]}],
        "learning_suggestions": ["Online suggestion"],
        "warnings": [],
        "metadata": {"model": "two-layer-test-model", "generated_at": "2026-10-16T00:00:00Z", "analyzed_source_count": material.source_count, "total_source_count": material.source_count},
    }


def _select(window, material):
    for row in range(window.library_list.count()):
        if window.library_list.item(row).data(Qt.ItemDataRole.UserRole) == material.id:
            window.library_list.setCurrentRow(row)
            QApplication.processEvents()
            assert window._material_id == material.id
            return
    raise AssertionError(f"material missing: {material.id}")


def _mode(window, mode):
    assert isinstance(window.mode_combo, QComboBox)
    index = window.mode_combo.findData(mode)
    assert index >= 0, mode
    window.mode_combo.setCurrentIndex(index)
    QApplication.processEvents()
    assert window.mode_combo.currentData() == mode


def _configure(window):
    window._base_url = "https://example.invalid/v1"
    window._model = "two-layer-test-model"
    window._api_key = "test-only-secret"


def _wait_idle(jobs, app):
    deadline = time.monotonic() + 5
    while jobs.busy and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    app.processEvents()
    assert not jobs.busy, "desktop worker did not become idle"


def _assert_export(window, tmp_path, stem, report):
    json_path = tmp_path / f"{stem}.json"
    md_path = tmp_path / f"{stem}.md"
    window.export_report(json_path)
    window.export_report(md_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["report"] == report
    assert payload["source_index"]
    assert all(source["text"] for source in payload["source_index"])
    markdown = md_path.read_text(encoding="utf-8")
    assert report["title"] in markdown
    assert payload["source_index"][0]["text"] in markdown
    return markdown


def test_default_local_report_is_real_exportable_and_never_saved(tmp_path, windows, monkeypatch):
    service = MaterialService(tmp_path / "db")
    material = _import(service)
    saves = []
    original = service.save_analysis

    def track_save(*args):
        saves.append(args)
        return original(*args)

    monkeypatch.setattr(service, "save_analysis", track_save)
    window = windows(service)
    assert window.mode_combo.currentData() == "local"
    assert isinstance(window._local_report, dict) and window._local_report["outline"]
    assert window._online_report is None
    assert window._report == window._local_report
    assert service.get_analysis(material.id) is None
    assert window.export_md_button.isEnabled() and window.export_json_button.isEnabled()
    markdown = _assert_export(window, tmp_path, "local", window._local_report)
    assert "LOCAL SOURCE" in markdown
    assert "本地规则" in markdown and "规则限制" in markdown and "来源索引" in markdown
    assert "AI 生成" not in markdown and "AI生成" not in markdown
    assert "## 分析信息" not in markdown and "已处理来源" not in markdown
    assert "识别标题" in window.report_meta_label.text()
    assert "已处理" not in window.report_meta_label.text()
    assert "需要智能整理" in window.report_views["knowledge"].toPlainText()
    assert "AI 生成" not in window.report_views["summary"].toPlainText()
    window.refresh_library(select_id=material.id)
    assert saves == []
    assert service.get_analysis(material.id) is None
    assert window.jobs.analysis_calls == []


def test_missing_configuration_leaves_local_report_exportable(tmp_path, windows):
    service = MaterialService(tmp_path / "db")
    material = _import(service)
    window = windows(service)
    before = copy.deepcopy(window._report)
    QTest.mouseClick(window.analyze_button, Qt.MouseButton.LeftButton)
    assert not window.jobs.busy
    assert window.jobs.analysis_calls == []
    assert window.mode_combo.currentData() == "local"
    assert window._report == before
    assert service.get_analysis(material.id) is None
    _assert_export(window, tmp_path, "unconfigured", before)


def test_client_factory_exception_preserves_local_export(tmp_path, windows, qt_app):
    service = MaterialService(tmp_path / "db")
    material = _import(service)
    calls = []

    def broken_factory(config):
        calls.append(config)
        raise RuntimeError("simulated client construction failure")

    jobs = DesktopJobs(service, client_factory=broken_factory)
    window = windows(service, jobs)
    before = copy.deepcopy(window._report)
    _configure(window)
    QTest.mouseClick(window.analyze_button, Qt.MouseButton.LeftButton)
    _wait_idle(jobs, qt_app)
    assert len(calls) == 1
    assert window.mode_combo.currentData() == "local"
    assert window._report == before
    assert window._online_report is None
    assert service.get_analysis(material.id) is None
    assert "失败" in window.status_label.text()
    _assert_export(window, tmp_path, "factory-failure", before)


def test_saved_ai_defaults_online_and_modes_export_independent_reports(tmp_path, windows):
    service = MaterialService(tmp_path / "db")
    material = _import(service)
    online = _online_report(service, material)
    service.save_analysis(material.id, online)
    window = windows(service)
    assert window.mode_combo.currentData() == "online"
    assert window._online_report == online
    assert window._report == online
    local = copy.deepcopy(window._local_report)
    assert local != online
    _assert_export(window, tmp_path, "online", online)
    _mode(window, "local")
    assert window._report == local
    assert "ONLINE ONLY" not in window.report_views["summary"].toPlainText()
    local_md = _assert_export(window, tmp_path, "switched-local", local)
    assert "ONLINE ONLY" not in local_md
    _mode(window, "online")
    assert window._report == online
    assert "ONLINE ONLY" in window.report_views["summary"].toPlainText()
    assert window._local_report == local
    assert service.get_analysis(material.id) == online


def test_manual_mode_is_remembered_per_material(tmp_path, windows):
    service = MaterialService(tmp_path / "db")
    first = _import(service, "first.md", "FIRST LOCAL")
    second = _import(service, "second.md", "SECOND LOCAL")
    first_online = _online_report(service, first, "FIRST ONLINE")
    second_online = _online_report(service, second, "SECOND ONLINE")
    service.save_analysis(first.id, first_online)
    service.save_analysis(second.id, second_online)
    window = windows(service)
    _select(window, first)
    assert window.mode_combo.currentData() == "online"
    _mode(window, "local")
    first_local = copy.deepcopy(window._report)
    _select(window, second)
    assert window.mode_combo.currentData() == "online"
    assert window._report == second_online
    _select(window, first)
    assert window.mode_combo.currentData() == "local"
    assert window._report == first_local
    assert "SECOND" not in json.dumps(window._report, ensure_ascii=False)
    window.refresh_library(select_id=first.id)
    assert window.mode_combo.currentData() == "local"
    _select(window, second)
    assert window.mode_combo.currentData() == "online"
    assert window._report == second_online


@pytest.mark.parametrize("mode", ["local", "online"])
def test_source_links_and_outline_selection_work_in_both_layers(tmp_path, windows, mode):
    service = MaterialService(tmp_path / "db")
    material = _import(service)
    service.save_analysis(material.id, _online_report(service, material))
    window = windows(service)
    _mode(window, mode)
    assert "source:" in window.report_views["outline"].toHtml()
    sources = service.get_sources(material.id)
    assert len(sources) >= 2, "navigation must leave a different source selection"
    target = sources[-1]
    window.source_list.setCurrentRow(0)
    window.report_views["outline"].anchorClicked.emit(QUrl(f"source:{target.id}"))
    assert window.source_list.currentItem().data(Qt.ItemDataRole.UserRole) == target.id
    assert window.source_preview.toPlainText() == target.text
    item = window.outline_tree.topLevelItem(0)
    assert item is not None
    refs = item.data(0, Qt.ItemDataRole.UserRole)
    assert refs
    window.outline_tree.setCurrentItem(item)
    QApplication.processEvents()
    assert window.source_list.currentItem().data(Qt.ItemDataRole.UserRole) == refs[0]
    assert window.mode_combo.currentData() == mode


def test_reopen_rebuilds_local_and_restores_saved_online_default(tmp_path, windows):
    root = tmp_path / "db"
    service = MaterialService(root)
    local_material = _import(service, "local.md", "UNSAVED LOCAL")
    ai_material = _import(service, "ai.md", "SAVED AI SOURCE")
    online = _online_report(service, ai_material)
    service.save_analysis(ai_material.id, online)
    first = windows(service)
    _select(first, local_material)
    local = copy.deepcopy(first._report)
    _select(first, ai_material)
    _mode(first, "local")
    first.close()
    reopened_service = MaterialService(root)
    second = windows(reopened_service)
    _select(second, ai_material)
    assert second.mode_combo.currentData() == "online"
    assert second._report == online
    _select(second, local_material)
    assert second.mode_combo.currentData() == "local"
    assert second._local_report["outline"] == local["outline"]
    assert second._online_report is None
    assert reopened_service.get_analysis(local_material.id) is None


def test_analysis_success_saves_online_and_switches_only_after_save(tmp_path, windows):
    service = MaterialService(tmp_path / "db")
    material = _import(service)
    window = windows(service)
    local = copy.deepcopy(window._local_report)
    _configure(window)
    QTest.mouseClick(window.analyze_button, Qt.MouseButton.LeftButton)
    assert window.jobs.busy
    online = _online_report(service, material, "NEW ONLINE")
    window.jobs.finish("succeeded", {"material_id": material.id, "report": online})
    assert service.get_analysis(material.id) == online
    assert window.mode_combo.currentData() == "online"
    assert window._online_report == online
    assert window._report == online
    assert window._local_report == local
    _mode(window, "local")
    assert window._report == local
    _assert_export(window, tmp_path, "after-success-local", local)


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "save-failed"])
@pytest.mark.parametrize("has_online", [False, True])
def test_unsuccessful_analysis_preserves_both_layers_and_manual_mode(
    tmp_path, windows, monkeypatch, outcome, has_online
):
    service = MaterialService(tmp_path / "db")
    material = _import(service)
    old_online = _online_report(service, material, "PREVIOUS ONLINE") if has_online else None
    if old_online is not None:
        service.save_analysis(material.id, old_online)
    window = windows(service)
    _mode(window, "local")
    local = copy.deepcopy(window._local_report)
    _configure(window)
    QTest.mouseClick(window.analyze_button, Qt.MouseButton.LeftButton)
    assert window.jobs.busy
    if outcome == "save-failed":
        def fail_save(*args):
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(service, "save_analysis", fail_save)
        window.jobs.finish("succeeded", {"material_id": material.id, "report": _online_report(service, material, "REJECTED ONLINE")})
        assert "保存失败" in window.status_label.text()
    elif outcome == "cancelled":
        QTest.mouseClick(window.cancel_button, Qt.MouseButton.LeftButton)
        assert window.jobs.cancel_calls == 1
        window.jobs.finish("cancelled")
    else:
        window.jobs.finish("failed")
    assert window.mode_combo.currentData() == "local"
    assert window._report == local
    assert window._local_report == local
    assert window._online_report == old_online
    assert service.get_analysis(material.id) == old_online
    assert window.export_json_button.isEnabled()
    _assert_export(window, tmp_path, "preserved-local", local)
    if has_online:
        _mode(window, "online")
        assert window._report == old_online
        _assert_export(window, tmp_path, "preserved-online", old_online)


def test_mode_selector_visible_and_reachable_at_900_by_600(tmp_path, windows, qt_app):
    service = MaterialService(tmp_path / "db")
    material = _import(service)
    service.save_analysis(material.id, _online_report(service, material))
    window = windows(service)
    for mode in ("local", "online"):
        _mode(window, mode)
        window.grab()
        qt_app.processEvents()
        assert window.size() == QSize(900, 600)
        available = QRect(window.mapToGlobal(window.contentsRect().topLeft()), window.contentsRect().size())
        for widget in (window.mode_combo, window.analyze_button, window.export_md_button, window.export_json_button):
            assert widget.isVisible() and widget.isEnabled()
            assert widget.width() > 0 and widget.height() > 0
            actual = QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())
            assert available.contains(actual), (widget.objectName(), actual, available)
        assert {window.mode_combo.itemData(i) for i in range(window.mode_combo.count())} == {"local", "online"}


def test_empty_online_mode_is_explicit_and_does_not_call_model(tmp_path, windows):
    service = MaterialService(tmp_path / "db")
    material = _import(service)
    window = windows(service)
    local = copy.deepcopy(window._local_report)
    _mode(window, "online")
    assert window._report is None and window._online_report is None
    assert "OpenAI" in window.report_views["summary"].toPlainText()
    assert "智能整理" in window.report_views["summary"].toPlainText()
    assert not window.export_md_button.isEnabled()
    assert window.analyze_button.isEnabled()
    assert window.jobs.analysis_calls == []
    assert service.get_analysis(material.id) is None
    _mode(window, "local")
    assert window._report == local and window.export_md_button.isEnabled()


@pytest.mark.parametrize("mode, suffix", [("local", "outline"), ("online", "analysis")])
def test_export_default_name_tracks_current_mode(tmp_path, windows, monkeypatch, mode, suffix):
    from PySide6.QtWidgets import QFileDialog
    service = MaterialService(tmp_path / "db")
    material = _import(service)
    service.save_analysis(material.id, _online_report(service, material))
    window = windows(service)
    _mode(window, mode)
    names = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (names.append(args[2]) or "", ""))
    window._choose_export("md")
    window._choose_export("json")
    assert names == [f"local-{suffix}.md", f"local-{suffix}.json"]


def test_fallback_is_labeled_original_segment_not_inferred_content(tmp_path, windows):
    service = MaterialService(tmp_path / "db")
    service.import_document("plain.txt", b"Plain prose without headings.")
    window = windows(service)
    assert window._report["metadata"]["outline_basis"] == "source_segments"
    item = window.outline_tree.topLevelItem(0)
    assert "原文分段" in item.text(0) and "自动归纳" not in item.text(0)
    markdown = _assert_export(window, tmp_path, "segments", window._report)
    assert "原文分段" in markdown and "自动归纳" not in markdown


def test_cancelled_or_wrong_material_success_cannot_replace_either_layer(tmp_path, windows):
    service = MaterialService(tmp_path / "db")
    material = _import(service)
    window = windows(service)
    local = copy.deepcopy(window._report)
    online = _online_report(service, material)
    window._on_job_succeeded("analysis", {"material_id": "wrong-id", "report": online})
    assert service.get_analysis(material.id) is None
    assert window._report == local and window._online_report is None
    _configure(window)
    window.start_analysis()
    window.cancel_job()
    window.jobs.finish("succeeded", {"material_id": material.id, "report": online})
    assert service.get_analysis(material.id) is None
    assert window._report == local and window._local_report == local
    assert window._online_report is None
