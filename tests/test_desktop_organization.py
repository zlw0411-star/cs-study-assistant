"""Offscreen organization/report regressions; all persistence stays in tmp_path."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QPlainTextEdit, QTextBrowser, QTreeWidget

from test_desktop_ui import FakeJobs, MaterialService, _report, _seed, _window, qt_app


@pytest.fixture
def make_window(qt_app):
    windows = []

    def make(service):
        window = _window(service, FakeJobs(service))
        windows.append(window)
        return window

    yield make
    for window in windows:
        window.close()
    qt_app.processEvents()


def _organized_report(sids):
    report = _report("组织报告 <b>不是粗体</b>", sids[0])
    report["outline"] = [
        {"title": "原文章节 <b>甲</b>", "summary": "章节说明 <i>乙</i>",
         "origin": "original", "source_ids": list(reversed(sids)), "children": [
             {"title": "推断子节", "summary": "子节说明", "origin": "inferred",
              "source_ids": [sids[0]], "children": []},
             {"title": "未标注来源类型的旧节点", "summary": "兼容节点",
              "source_ids": [sids[0]], "children": []},
         ]},
    ]
    report["knowledge_points"] = [{
        "name": "概念 <b>丙</b>", "explanation": "旧版说明 <i>丁</i>",
        "chapter": "所属章节 <b>戊</b>", "definition": "定义 <i>己</i>",
        "key_points": ["要点 <b>庚</b>", "另一要点 & 细节"],
        "formulas": ["公式 <i>辛</i>: x < y & z"],
        "conditions": ["适用条件 <b>壬</b>"], "source_ids": sids,
    }]
    report["metadata"].update(analyzed_source_count=len(sids), total_source_count=len(sids),
                              referenced_source_count=len(sids) - 1, unreferenced_source_ids=[sids[-1]])
    return report


@pytest.fixture
def organized(tmp_path):
    service = MaterialService(tmp_path / "db")
    material = service.import_document("organization.txt", ("第一段原文。" * 1000 + "\n\n第二段原文。").encode()).material
    sources = service.get_sources(material.id)
    assert len(sources) >= 2
    report = _organized_report([source.id for source in sources])
    service.save_analysis(material.id, report)
    return service, material, sources, report


def _is_inferred(text):
    return any(word in text.lower() for word in ("inferred", "推断", "推导", "推测", "自动归纳"))


def _is_original(text):
    return any(word in text.lower() for word in ("original", "原文", "原始", "原有"))


def test_outline_tree_origin_defaults_and_click_first_source(organized, make_window, qt_app):
    service, _, sources, report = organized
    window = make_window(service)
    tree = window.outline_tree
    assert isinstance(tree, QTreeWidget)
    assert tree.topLevelItemCount() == 1
    root = tree.topLevelItem(0)
    assert root.childCount() == 2
    assert root.data(0, Qt.ItemDataRole.UserRole) == report["outline"][0]["source_ids"]

    def item_text(item):
        return " ".join(item.text(column) + " " + item.toolTip(column) for column in range(tree.columnCount()))

    assert "原文章节 <b>甲</b>" in item_text(root)
    assert _is_original(item_text(root))
    assert _is_inferred(item_text(root.child(0)))
    assert _is_inferred(item_text(root.child(1)))  # absent origin is inferred, not original
    tree.expandAll()
    page = tree
    while page is not None and window.report_tabs.indexOf(page) < 0:
        page = page.parentWidget()
    assert page is not None, "outline tree must be inside a report tab"
    window.report_tabs.setCurrentWidget(page)
    qt_app.processEvents()
    window.source_list.setCurrentRow(0)
    # Exercise the real view click, not a direct call to the navigation handler.
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualItemRect(root).center())
    qt_app.processEvents()
    assert window.source_list.currentItem().data(Qt.ItemDataRole.UserRole) == sources[-1].id
    assert window.source_preview.toPlainText() == sources[-1].text


def test_knowledge_fields_are_visible_and_html_escaped(organized, make_window):
    service, _, _, report = organized
    window = make_window(service)
    view = window.report_views["knowledge"]
    text = view.toPlainText()
    point = report["knowledge_points"][0]
    for key in ("name", "chapter", "definition", "explanation"):
        assert point[key] in text, key
    for key in ("key_points", "formulas", "conditions"):
        for value in point[key]:
            assert value in text, key
    # Literal tags survive as text instead of being interpreted as rich markup.
    rendered = view.toHtml()
    assert "&lt;b&gt;" in rendered and "&lt;i&gt;" in rendered
    assert report["title"] in window.report_views["summary"].toPlainText()


def test_metadata_describes_processing_not_semantic_coverage(organized, make_window, tmp_path):
    service, _, sources, _ = organized
    window = make_window(service)
    metadata = window.report_meta_label.text()
    assert f"{len(sources)}/{len(sources)}" in metadata
    assert "来源覆盖" not in metadata
    assert any(word in metadata.lower() for word in ("processed", "处理", "分析"))
    path = tmp_path / "metadata.md"
    window.export_report(path)
    markdown = path.read_text(encoding="utf-8")
    assert "来源覆盖" not in markdown
    assert "非语义覆盖率" in markdown and "非语义覆盖率" in metadata
    assert "fake-model" in markdown
    for text in (metadata, markdown):
        assert f"已引用来源：{len(sources) - 1}" in text or f"已引用来源 {len(sources) - 1}" in text
        assert "未引用来源" in text and sources[-1].id in text


def test_exports_preserve_organization_fields_and_full_source_index(organized, make_window, tmp_path):
    service, _, sources, report = organized
    window = make_window(service)
    md_path, json_path = tmp_path / "complete.md", tmp_path / "complete.json"
    window.export_report(md_path)
    window.export_report(json_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["report"] == report
    assert payload["source_index"] == [
        {"id": source.id, "locator": source.locator, "page": source.page,
         "paragraph": source.paragraph, "text": source.text} for source in sources
    ]
    markdown = md_path.read_text(encoding="utf-8")
    point = report["knowledge_points"][0]
    for key in ("name", "chapter", "definition", "explanation"):
        assert point[key] in markdown
    for key in ("key_points", "formulas", "conditions"):
        assert all(value in markdown for value in point[key])
    assert _is_original(markdown) and _is_inferred(markdown)
    root = report["outline"][0]
    for node in [root, *root["children"]]:
        assert node["title"] in markdown and node["summary"] in markdown
    for source in sources:
        assert source.id in markdown and source.locator in markdown and source.text in markdown
    assert "AI 生成内容" in markdown


def test_optional_empty_fields_and_chapter_grouping(tmp_path, make_window):
    service = MaterialService(tmp_path / "db")
    material, source, report = _seed(service)
    report["knowledge_points"] = [
        {"name": "甲概念", "explanation": "甲说明", "chapter": "第一章", "source_ids": [source.id]},
        {"name": "乙概念", "explanation": "乙说明", "chapter": "第二章", "source_ids": [source.id]},
        {"name": "丙概念", "explanation": "丙说明", "chapter": "第一章", "definition": "", "key_points": [], "formulas": [], "conditions": [], "source_ids": [source.id]},
    ]
    service.save_analysis(material.id, report)
    window = make_window(service)
    text = window.report_views["knowledge"].toPlainText()
    assert text.index("第一章") < text.index("甲概念") < text.index("丙概念") < text.index("第二章") < text.index("乙概念")
    assert "AI" in text and "非原文" in text
    for label in ("定义", "要点", "公式", "适用条件"):
        assert label not in text
    window.export_report(tmp_path / "grouped.md")
    markdown = (tmp_path / "grouped.md").read_text(encoding="utf-8")
    assert markdown.count("### 第一章") == 1
    for label in ("定义：", "要点：", "公式：", "适用条件："):
        assert label not in markdown


def test_legacy_report_still_renders_exports_and_navigates(tmp_path, make_window):
    service = MaterialService(tmp_path / "db")
    _, source, report = _seed(service)
    window = make_window(service)
    assert "Explanation" in window.report_views["knowledge"].toPlainText()
    assert report["title"] in window.report_views["summary"].toPlainText()
    window.report_views["knowledge"].anchorClicked.emit(QUrl(f"source:{source.id}"))
    assert window.source_preview.toPlainText() == source.text
    for extension in ("md", "json"):
        path = tmp_path / f"legacy.{extension}"
        window.export_report(path)
        text = path.read_text(encoding="utf-8")
        assert "Explanation" in text and source.id in text
    assert json.loads((tmp_path / "legacy.json").read_text(encoding="utf-8"))["report"] == report


@pytest.mark.parametrize("stage", ["get_material", "get_sources", "get_analysis"])
@pytest.mark.parametrize("error", [ValueError, OSError, sqlite3.OperationalError])
@pytest.mark.parametrize("route", ["initial", "refresh", "selection"])
def test_load_failure_clears_stale_state_or_keeps_valid_local_sources(
    tmp_path, make_window, monkeypatch, stage, error, route,
):
    service = MaterialService(tmp_path / "db")
    material, _, _ = _seed(service)
    window = None if route == "initial" else make_window(service)
    if window is not None:
        assert window._material_id == material.id and window._report and window._sources

    def fail(*args, **kwargs):
        raise error("simulated local read failure")

    monkeypatch.setattr(service, stage, fail)
    if route == "initial":
        window = make_window(service)
    elif route == "refresh":
        window.refresh_library(select_id=material.id)
    else:
        # Drive the selection slot through its signal, as a user selection does.
        item = window.library_list.currentItem()
        window.library_list.currentItemChanged.emit(item, None)
    if stage == "get_analysis":
        assert window._material_id == material.id
        assert window._online_report is None
        assert window._report == window._local_report
        assert window._report["metadata"]["mode"] == "local"
        assert window._sources and window.source_preview.toPlainText()
        assert window.export_md_button.isEnabled()
        assert "智能报告读取失败" in window.status_label.text()
        return
    assert window._material_id is None
    assert window._report is None
    assert window._online_report is None
    assert window._local_report is None
    assert not window.mode_combo.isEnabled()
    assert window._sources == []
    assert not window._source_by_id
    assert window.source_list.count() == 0
    assert window.source_preview.toPlainText() == ""
    assert not window.analyze_button.isEnabled()
    assert not window.export_md_button.isEnabled()
    assert not window.export_json_button.isEnabled()
    status = window.status_label.text()
    assert any(word in status for word in ("失败", "无法", "错误"))
    assert "已载入" not in status


@pytest.mark.parametrize("extension", ["md", "json"])
@pytest.mark.parametrize("requested_name", ["report", "report.wrong"])
def test_cancel_overwrite_checks_corrected_final_destination(
    tmp_path, make_window, monkeypatch, extension, requested_name,
):
    service = MaterialService(tmp_path / "db")
    _seed(service)
    window = make_window(service)
    destination = tmp_path / f"report.{extension}"
    destination.write_bytes(b"KEEP original export\x00\xff")
    before = set(tmp_path.iterdir())
    questions = []

    def cancel(*args, **kwargs):
        questions.append((args, kwargs))
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", cancel)
    window.export_report(tmp_path / requested_name, extension)
    assert questions, "the corrected destination already exists and must be confirmed"
    assert destination.read_bytes() == b"KEEP original export\x00\xff"
    assert set(tmp_path.iterdir()) == before  # no wrong-suffix export or leaked temp file
    assert "已导出" not in window.status_label.text()


def test_all_material_warnings_are_available(tmp_path, make_window, monkeypatch, qt_app):
    service = MaterialService(tmp_path / "db")
    material, _, _ = _seed(service)
    warnings = [f"警告 {index}: " + "请完整核对原文。" * 20 for index in range(12)]
    original_get = service.get_material
    monkeypatch.setattr(service, "get_material", lambda mid: replace(original_get(mid), warnings=warnings))
    window = make_window(service)
    assert all(warning in window.warning_label.text() for warning in warnings)
    button = window.warning_button
    assert button.isVisible()
    captured = []

    def information(*args, **kwargs):
        captured.append("\n".join(str(value) for value in args[1:]))
        return QMessageBox.StandardButton.Ok

    def dialog_exec(dialog):
        texts = [dialog.windowTitle()]
        for widget in dialog.findChildren(QLabel) + dialog.findChildren(QTextBrowser) + dialog.findChildren(QPlainTextEdit):
            texts.append(widget.toPlainText() if hasattr(widget, "toPlainText") else widget.text())
        if isinstance(dialog, QMessageBox):
            texts += [dialog.text(), dialog.informativeText(), dialog.detailedText()]
        captured.append("\n".join(texts))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QMessageBox, "information", information)
    monkeypatch.setattr(QMessageBox, "warning", information)
    monkeypatch.setattr(QDialog, "exec", dialog_exec)
    monkeypatch.setattr(QMessageBox, "exec", dialog_exec)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    qt_app.processEvents()
    assert captured, "warning button must open a view containing all warnings"
    assert all(warning in "\n".join(captured) for warning in warnings)
