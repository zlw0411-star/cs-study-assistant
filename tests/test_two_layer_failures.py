"""Degraded online storage must not disable valid offline navigation."""
import json
import sqlite3

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QRect

from test_two_layer_desktop import (
    windows, qt_app, offline_session, _import, _online_report, _mode,
)
from study_assistant.materials import MaterialService


@pytest.mark.parametrize("stored", [
    "{broken", "[]", '"not a report"', '{"outline": null}',
    '{"outline": [{"children": null}]}',
    '{"metadata": {"unreferenced_source_ids": null}}',
    '{"knowledge_points": [{"chapter": 123}]}',
    '{"outline": [{"source_ids": [123]}]}',
])
def test_damaged_smart_report_falls_back_without_modifying_database(tmp_path, windows, stored):
    service = MaterialService(tmp_path / "library")
    material = _import(service)
    service.save_analysis(material.id, _online_report(service, material))
    with sqlite3.connect(service.db_path) as db:
        db.execute("UPDATE material_analyses SET report=? WHERE material_id=?", (stored, material.id))
    window = windows(service)
    assert window._material_id == material.id
    assert window.mode_combo.currentData() == "local"
    assert window._online_report is None
    assert window._local_report["outline"]
    assert "智能报告读取失败" in window.status_label.text()
    assert "智能报告读取失败" in window.report_views["summary"].toPlainText()
    window.export_report(tmp_path / "safe-outline.md")
    window.export_report(tmp_path / "safe-outline.json")
    payload = json.loads((tmp_path / "safe-outline.json").read_text(encoding="utf-8"))
    assert payload["report"]["metadata"]["mode"] == "local"
    assert payload["source_index"][0]["text"]
    with sqlite3.connect(service.db_path) as db:
        assert db.execute("SELECT report FROM material_analyses WHERE material_id=?", (material.id,)).fetchone()[0] == stored
    _mode(window, "online")
    assert window._report is None
    assert not window.export_md_button.isEnabled()
    _mode(window, "local")
    assert window.export_md_button.isEnabled()


def test_mode_status_and_cost_notice_are_visible_before_any_request(tmp_path, windows, qt_app):
    service = MaterialService(tmp_path / "library")
    material = _import(service)
    service.save_analysis(material.id, _online_report(service, material))
    window = windows(service)
    assert "智能整理报告" in window.status_label.text()
    _mode(window, "local")
    assert "本地目录" in window.status_label.text() and "未调用模型" in window.status_label.text()
    assert window.report_tabs.currentIndex() == 1
    _mode(window, "online")
    assert "智能整理报告" in window.status_label.text()
    assert "在线" in window.analyze_button.text()
    assert "计费" in window.analyze_button.toolTip()
    notice = window.online_notice_label
    assert notice.isVisible() and "全部提取文字" in notice.text() and "多次计费" in notice.text()
    qt_app.processEvents()
    available = QRect(window.mapToGlobal(window.contentsRect().topLeft()), window.contentsRect().size())
    assert available.contains(QRect(notice.mapToGlobal(QPoint(0, 0)), notice.size()))
    assert window.jobs.analysis_calls == []
