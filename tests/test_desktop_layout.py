from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, Signal, QUrl
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from study_assistant.desktop import MainWindow, ModelSettingsDialog
from study_assistant.materials import MaterialService


class LayoutJobs(QObject):
    """Small synchronous job double that keeps the UI task contract intact."""

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

    def start_import(self, _path):
        self.busy = True
        self.job_kind = "import"
        return True

    def start_analysis(self, _material_id, _config):
        self.busy = True
        self.job_kind = "analysis"
        return True

    def cancel(self):
        self.cancel_calls += 1

    def finish_cancel(self):
        kind = self.job_kind
        self.busy = False
        self.job_kind = None
        self.cancelled.emit(kind)
        self.idle.emit()


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    # Use an installed CJK font for realistic label measurement in offscreen Qt.
    for font_path in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ):
        if font_path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            if families:
                app.setFont(QFont(families[0], 9))
                break
    yield app
    app.processEvents()


def _window(service, jobs, app):
    window = MainWindow(service=service, jobs=jobs)
    window.resize(900, 600)
    window.show()
    app.processEvents()
    window.grab()  # Force a real Qt layout/render pass before reading geometry.
    app.processEvents()
    return window


def _assert_inside_window(window, widgets):
    available = QRect(
        window.mapToGlobal(window.contentsRect().topLeft()),
        window.contentsRect().size(),
    )
    for widget in widgets:
        assert widget.isVisible(), widget.objectName()
        assert widget.width() > 0 and widget.height() > 0, widget.objectName()
        actual = QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())
        assert available.contains(actual), (widget.objectName(), actual, available)


def _select_material(window, material_id):
    for row in range(window.library_list.count()):
        item = window.library_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == material_id:
            window.library_list.setCurrentRow(row)
            return item
    raise AssertionError(f"material {material_id} not visible in library")


def test_compact_window_keeps_primary_controls_reachable(tmp_path, qt_app):
    service = MaterialService(tmp_path / "db")
    jobs = LayoutJobs(service)
    window = _window(service, jobs, qt_app)
    controls = [
        window.import_button,
        window.model_settings_button,
        window.analyze_button,
        window.export_md_button,
        window.export_json_button,
        window.empty_import_button,
    ]
    _assert_inside_window(window, controls)
    assert window.size() == QSize(900, 600), window.size()
    assert window.empty_art.size().width() > 0 and window.empty_art.size().height() > 0

    # Busy state exposes a reachable cancel action while guarding other actions.
    material = service.import_document("compact.txt", b"A short lesson.").material
    window.refresh_library()
    window._base_url, window._model, window._api_key = "https://model.example/v1", "test-model", "test-key"
    assert jobs.start_analysis(material.id, object())
    window._update_controls()
    qt_app.processEvents()
    _assert_inside_window(window, [window.analyze_button, window.cancel_button, window.export_md_button, window.export_json_button])
    assert window.size() == QSize(900, 600), window.size()
    assert not window.import_button.isEnabled()
    window.cancel_job()
    assert jobs.cancel_calls == 1
    jobs.finish_cancel()
    qt_app.processEvents()
    assert "停止" in window.status_label.text()
    assert not window.cancel_button.isVisible()
    window.close()


def test_empty_guidance_long_name_many_sources_and_report_switch(tmp_path, qt_app):
    service = MaterialService(tmp_path / "db")
    jobs = LayoutJobs(service)
    empty = _window(service, jobs, qt_app)
    assert empty.library_list.count() == 0
    assert empty.empty_art.isVisible()
    assert empty.empty_title.text().strip()
    assert empty.empty_copy.text().strip()
    assert all(view.toPlainText().strip() for view in empty.report_views.values())
    empty.close()

    filename = "课程资料" + ("A" * 140) + ".txt"
    long_material = service.import_document(filename, ("第一段\n\n" + "x" * 6500).encode()).material
    first_source = service.get_sources(long_material.id)[0]
    service.save_analysis(long_material.id, {
        "title": "已保存报告",
        "summary": "第一份资料的摘要。",
        "outline": [{"title": "章节", "summary": "内容", "source_ids": [first_source.id], "children": []}],
        "knowledge_points": [],
        "learning_suggestions": [],
        "warnings": [],
        "metadata": {"model": "test-model", "analyzed_source_count": 1, "total_source_count": 4},
    })
    no_report = service.import_document("第二份.md", "第二份资料正文".encode()).material
    window = _window(service, jobs, qt_app)
    initial_item = window.library_list.currentItem()
    assert initial_item is not None
    assert window.library_list.itemWidget(initial_item).property("active") is True
    item = _select_material(window, long_material.id)
    qt_app.processEvents()
    window.grab()
    assert len(item.text()) <= 180
    assert item.toolTip() == filename
    assert window.file_title_label.toolTip() == filename
    assert window.source_list.count() == 4
    assert window.library_list.visualItemRect(item).height() > 0
    card = window.library_list.itemWidget(item)
    assert card is not None and card.objectName() == "libraryCard"
    assert card.width() > 0 and card.height() > 0
    assert card.width() <= window.library_list.viewport().width()
    other_item = _select_material(window, no_report.id)
    qt_app.processEvents()
    assert window.library_list.itemWidget(other_item).property("active") is True
    assert window.library_list.itemWidget(item).property("active") is False
    _select_material(window, long_material.id)
    qt_app.processEvents()
    assert window.library_list.itemWidget(item).property("active") is True
    assert window.library_list.itemWidget(other_item).property("active") is False
    assert "已保存报告" in window.report_views["summary"].toPlainText()

    # A multi-source document remains navigable in the compact split layout.
    window.source_list.setCurrentRow(3)
    qt_app.processEvents()
    assert window.size() == QSize(900, 600), window.size()
    assert window.report_tabs.mapToGlobal(QPoint(0, 0)).x() < window.source_preview.mapToGlobal(QPoint(0, 0)).x()
    window.report_tabs.setCurrentIndex(1)
    qt_app.processEvents()
    _assert_inside_window(window, [window.outline_tree, window.report_tabs, window.source_preview])
    assert len(window.source_preview.toPlainText()) <= 3000
    assert window.source_list.currentItem().data(Qt.ItemDataRole.UserRole) == "S4"
    window.report_views["outline"].anchorClicked.emit(QUrl(f"source:{first_source.id}"))
    qt_app.processEvents()
    assert window.source_list.currentItem().data(Qt.ItemDataRole.UserRole) == first_source.id
    assert window.source_preview.toPlainText() == first_source.text

    _select_material(window, no_report.id)
    qt_app.processEvents()
    summary = window.report_views["summary"].toPlainText()
    assert window._report == window._local_report
    assert "本地规则" in summary and "未调用模型" in window.report_meta_label.text()
    assert window.export_md_button.isEnabled() and window.export_json_button.isEnabled()
    assert "已保存报告" not in summary
    for view in window.report_views.values():
        assert view.toPlainText().strip()
        assert "已保存报告" not in view.toPlainText()
    assert window.source_list.count() == 1
    assert "第二份资料正文" in window.source_preview.toPlainText()
    _assert_inside_window(window, [
        window.import_button,
        window.model_settings_button,
        window.analyze_button,
        window.source_hint,
        window.source_list,
        window.source_preview,
        window.report_tabs,
        window.mode_combo,
        window.status_label,
    ])
    window.close()
