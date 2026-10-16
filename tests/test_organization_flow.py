"""Offline end-to-end acceptance: real documents, workers, HTTP protocol and Qt.

Only the OpenAI endpoint is simulated; no extractor/analyzer/storage/UI is faked.
ASCII document text also works with the existing dependency-free PDF fixture.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict
import json
import os
import time

import httpx
import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from study_assistant.analysis import OnlineAnalysisClient
from study_assistant.desktop import MainWindow
from study_assistant.desktop_jobs import DesktopJobs
from study_assistant.materials import MaterialService
from test_desktop_ui import qt_app  # reuse QApplication lifecycle, not FakeJobs
from test_material_extraction import _docx, _paragraph, _pdf


KEY = "sk-OFFLINE-organization-flow-secret-20261016"
MODEL = "offline-organization-model"


def _sections(owner):
    return [
        {"chapter": f"{owner} Chapter {i}", "name": "Rate",
         "definition": f"{owner} rate means quantity per interval {i}.",
         "explanation": f"{owner} interval {i} has its own rate rule.",
         "key_points": [f"{owner} keep interval {i} separate."],
         "formulas": [f"r{i} = q / t{i}"],
         "conditions": [f"t{i} > 0; exclude interval {3 - i}."]}
        for i in (1, 2)
    ]


def _document_bytes(kind, sections):
    blocks = []
    for point in sections:
        details = [point["definition"], point["explanation"],
                   *point["key_points"], *point["formulas"], *point["conditions"]]
        # Two bounded sources even in paragraph-packed TXT/MD/DOCX, so source
        # navigation must actually change selection rather than stay on S1.
        details.append((point["chapter"] + " evidence. ") * 65)
        blocks.append((point["chapter"], "\n".join(details)))
    if kind == "pdf":
        return _pdf(*(heading + "\n" + body for heading, body in blocks))
    if kind == "docx":
        return _docx("".join(_paragraph(heading + "\n" + body, "Heading1")
                             for heading, body in blocks))
    return "\n\n".join(("# " if kind == "md" else "") + heading + "\n" + body
                         for heading, body in blocks).encode("utf-8")


def _wait_idle(app, jobs, failures):
    deadline = time.monotonic() + 10
    while jobs.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert not jobs.busy, "real DesktopJobs worker did not finish"
    assert not failures, failures


def _select(window, material_id, app):
    for row in range(window.library_list.count()):
        if window.library_list.item(row).data(Qt.ItemDataRole.UserRole) == material_id:
            window.library_list.setCurrentRow(row)
            app.processEvents()
            return
    pytest.fail(f"material missing from library: {material_id}")


def _assert_report(report, sections, sources):
    expected_points = []
    expected_outline = []
    for point in sections:
        refs = [s.id for s in sources if point["chapter"] in s.text]
        assert len(refs) == 1
        expected_points.append({**point, "source_ids": refs})
        expected_outline.append({
            "title": point["chapter"], "origin": "original",
            "summary": point["explanation"], "source_ids": refs,
            "children": [{"title": point["chapter"] + " review", "origin": "inferred",
                          "summary": point["definition"], "source_ids": refs, "children": []}],
        })
    assert report["knowledge_points"] == expected_points  # same name, distinct chapters
    assert report["outline"] == expected_outline
    meta = report["metadata"]
    assert meta["model"] == MODEL
    assert meta["generated_at"]
    assert meta["analyzed_source_count"] == meta["total_source_count"] == len(sources)
    assert meta["referenced_source_count"] == len(sources)
    assert meta["unreferenced_source_ids"] == []
    assert "不代表" in meta["coverage_basis"]


@pytest.mark.parametrize("kind", ["txt", "md", "docx", "pdf"])
def test_offline_organization_flow(tmp_path, qt_app, monkeypatch, kind):
    # Guard against accidental use of a default real transport, even if the
    # injected client wiring regresses. All storage/settings are test-local.
    def deny_network(*args, **kwargs):
        pytest.fail("real HTTP transport is forbidden in offline acceptance")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_network)
    monkeypatch.setenv("CSA_DATA_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("CSA_ANALYSIS_BASE_URL", "https://offline.invalid/v1")
    monkeypatch.setenv("CSA_ANALYSIS_MODEL", MODEL)
    monkeypatch.setenv("CSA_ANALYSIS_API_KEY", KEY)
    service = MaterialService(tmp_path / "library")
    requests = []
    active = {}
    failures = []
    saved = []
    local_reports = {}

    def respond(request):
        assert request.method == "POST"
        assert str(request.url) == "https://offline.invalid/v1/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        payload = json.loads(request.content)
        assert payload["model"] == MODEL
        assert [m["role"] for m in payload["messages"]] == ["system", "user"]
        prompt = payload["messages"][1]["content"]
        assert KEY not in prompt
        assert active["other"] not in prompt  # never send the other file's text
        if "资料数据：" in prompt:
            stage = "map"
            data = json.loads(prompt.split("资料数据：", 1)[1])
            assert data == [{"id": s.id, "locator": s.locator, "text": s.text}
                            for s in active["sources"]]
            outline, points = [], []
            for point in active["sections"]:
                evidence = next(s for s in data if point["chapter"] in s["text"])
                for value in [point["definition"], point["explanation"],
                              *point["key_points"], *point["formulas"], *point["conditions"]]:
                    assert value in evidence["text"]
                refs = [evidence["id"]]
                points.append({**point, "source_ids": refs})
                outline.append({"title": point["chapter"], "origin": "original",
                                "summary": point["explanation"], "source_ids": refs,
                                "children": [{"title": point["chapter"] + " review",
                                              "origin": "inferred", "summary": point["definition"],
                                              "source_ids": refs, "children": []}]})
            result = {"coverage_source_ids": [s["id"] for s in data],
                      "summary": active["owner"] + " source-only notes",
                      "outline": outline, "knowledge_points": points}
            active["mapped"] = result
        else:
            stage = "final"
            assert active["filename"] in prompt
            # Echo validated map data, not a pre-seeded final report. Losing a
            # new field between stages is therefore observable in the output.
            analyses = json.loads(prompt.split("分析数据：", 1)[1])
            assert analyses == [active["mapped"]]
            result = {k: analyses[0][k] for k in ("summary", "outline", "knowledge_points")}
            result.update(title=active["owner"] + " study report", material_type="讲义/文档",
                          learning_suggestions=["核对本资料公式及适用条件。"], warnings=[])
        requests.append((active["owner"], stage))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps(result, ensure_ascii=False)}}]})

    with ExitStack() as stack:
        def client_factory(config):
            client = stack.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
            return OnlineAnalysisClient(config, client=client)

        jobs = DesktopJobs(service, client_factory=client_factory)
        jobs.failed.connect(lambda kind, message: failures.append((kind, message)))
        window = MainWindow(service=service, jobs=jobs)
        window.show()
        qt_app.processEvents()
        try:
            for owner, other in (("ALPHA", "BETA"), ("BETA", "ALPHA")):
                before_requests = len(requests)
                sections = _sections(owner)
                raw = _document_bytes(kind, sections)
                path = tmp_path / f"{owner}.{kind}"
                path.write_bytes(raw)
                assert window._start_import_path(path)
                _wait_idle(qt_app, jobs, failures)
                material_id = window._material_id
                material = service.get_material(material_id)
                sources = service.get_sources(material_id)
                assert material.kind == kind and material.filename == path.name
                assert service.get_raw(material_id) == raw
                assert len(sources) == 2
                assert service.get_analysis(material_id) is None
                assert all(other not in s.text for s in sources)
                if kind == "pdf":
                    assert [s.page for s in sources] == [1, 2]
                    assert material.warnings  # readable PDF still has extraction caveats
                else:
                    assert all(s.page is None and s.paragraph for s in sources)
                # The first layer is usable before any model request and is
                # never written into the smart-report table.
                assert window.mode_combo.currentData() == "local"
                local_reports[material_id] = window._report
                assert window._report["metadata"]["mode"] == "local"
                assert window._report["knowledge_points"] == []
                assert window._report["outline"]
                assert len(requests) == before_requests
                window.export_report(tmp_path / f"{owner}-local.md")
                window.export_report(tmp_path / f"{owner}-local.json")
                local_payload = json.loads((tmp_path / f"{owner}-local.json").read_text(encoding="utf-8"))
                assert local_payload["report"] == local_reports[material_id]
                assert local_payload["source_index"] == [asdict(s) for s in sources]
                assert "AI 生成内容" not in (tmp_path / f"{owner}-local.md").read_text(encoding="utf-8")
                assert service.get_analysis(material_id) is None
                assert len(requests) == before_requests
                active.clear()
                active.update(owner=owner, other=other, sections=sections,
                              sources=sources, filename=path.name)
                QTest.mouseClick(window.analyze_button, Qt.MouseButton.LeftButton)
                _wait_idle(qt_app, jobs, failures)
                report = service.get_analysis(material_id)
                _assert_report(report, sections, sources)
                assert report == window._report
                assert all(w in report["warnings"] for w in material.warnings)
                assert owner in window.report_views["summary"].toPlainText()
                knowledge = window.report_views["knowledge"].toPlainText()
                for point in sections:
                    for key in ("chapter", "definition", "explanation"):
                        assert point[key] in knowledge
                    for key in ("key_points", "formulas", "conditions"):
                        assert all(value in knowledge for value in point[key])
                assert other not in knowledge
                assert "非语义覆盖率" in window.report_meta_label.text()

                tree = window.outline_tree
                assert tree.topLevelItemCount() == 2
                root = tree.topLevelItem(1)
                assert sections[1]["chapter"] in root.text(0) and "原文标题" in root.text(0)
                assert "自动归纳" in root.child(0).text(0)
                window.report_tabs.setCurrentWidget(tree.parentWidget())
                qt_app.processEvents()
                window.source_list.setCurrentRow(0)
                QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                                 pos=tree.visualItemRect(root).center())
                qt_app.processEvents()
                assert window.source_list.currentItem().data(Qt.ItemDataRole.UserRole) == sources[1].id
                assert window.source_preview.toPlainText() == sources[1].text
                assert other not in window.source_preview.toPlainText()

                md_path, json_path = tmp_path / f"{owner}-report.md", tmp_path / f"{owner}-report.json"
                window.export_report(md_path)
                window.export_report(json_path)
                markdown = md_path.read_text(encoding="utf-8")
                exported = json.loads(json_path.read_text(encoding="utf-8"))
                assert exported["report"] == report  # includes origin and all metadata
                assert exported["material"]["filename"] == material.filename
                assert exported["source_index"] == [asdict(s) for s in sources]
                for point in sections:
                    for key in ("chapter", "definition", "explanation"):
                        assert point[key] in markdown
                    for key in ("key_points", "formulas", "conditions"):
                        assert all(value in markdown for value in point[key])
                for source in sources:
                    assert source.text in markdown and source.locator in markdown
                for value in (MODEL, report["metadata"]["generated_at"], "原文标题", "自动归纳", "AI 生成内容"):
                    assert value in markdown
                assert other not in markdown and other not in json_path.read_text(encoding="utf-8")
                saved.append((material_id, sections, sources, report, md_path, json_path))

            assert requests == [(owner, stage) for owner in ("ALPHA", "BETA") for stage in ("map", "final")]
            assert saved[0][0] != saved[1][0]
            assert [s.id for s in saved[0][2]] == [s.id for s in saved[1][2]] == ["S1", "S2"]
            # Switch back after analyzing BETA: reports and S1/S2 bindings must
            # remain material-scoped, not just the most recently generated data.
            for material_id, _, sources, report, _, _ in saved:
                _select(window, material_id, qt_app)
                assert window._report == report
                assert window.source_preview.toPlainText() == sources[0].text
        finally:
            jobs.cancel()
            _wait_idle(qt_app, jobs, [])
            window.close()
            qt_app.processEvents()

    reopened = MaterialService(service.data_dir)
    assert len(reopened.list_materials()) == 2
    restart = MainWindow(service=reopened, jobs=DesktopJobs(reopened, client_factory=client_factory))
    try:
        for material_id, sections, sources, report, _, _ in saved:
            assert reopened.get_analysis(material_id) == report
            assert reopened.get_sources(material_id) == sources
            _assert_report(reopened.get_analysis(material_id), sections, sources)
            _select(restart, material_id, qt_app)
            assert restart._report == report
            assert report["title"] in restart.report_views["summary"].toPlainText()
            restart.mode_combo.setCurrentIndex(restart.mode_combo.findData("local"))
            assert restart._report == local_reports[material_id]
            assert reopened.get_analysis(material_id) == report
        # Scan actual DB bytes (including raw BLOBs), journals and both exports.
        for path in [*service.data_dir.rglob("*"), *tmp_path.glob("*-report.*"), *tmp_path.glob("*-local.*")]:
            if path.is_file():
                assert KEY.encode() not in path.read_bytes(), path
    finally:
        restart.close()
        qt_app.processEvents()
