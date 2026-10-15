from pathlib import Path
import json

import httpx
import pytest
import streamlit as st

from study_assistant.materials import MaterialService
from study_assistant.analysis import MaterialAnalyzer


def _report(title: str, source_id: str) -> dict:
    return {
        "title": title,
        "material_type": "课程笔记",
        "summary": f"{title} 摘要",
        "outline": [{"title": "第一部分", "summary": "主要内容", "source_ids": [source_id], "children": []}],
        "knowledge_points": [{"name": "核心概念", "explanation": "概念说明", "source_ids": [source_id]}],
        "learning_suggestions": ["尝试复述核心概念"],
        "warnings": [],
        "metadata": {"model": "test-model", "generated_at": "2026-01-01T00:00:00Z", "analyzed_source_count": 1, "total_source_count": 1},
    }


def _app(monkeypatch, data_dir):
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("CSA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("CSA_ANALYSIS_BASE_URL", raising=False)
    monkeypatch.delenv("CSA_ANALYSIS_MODEL", raising=False)
    return streamlit_testing.AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15
    ).run()


def test_material_app_empty_state_and_unconfigured_analysis(tmp_path, monkeypatch):
    app = _app(monkeypatch, tmp_path / "empty")
    assert not app.exception
    assert any("通用资料分析助手" in item.value for item in app.markdown)
    assert any("资料库为空" in item.value for item in app.info)
    assert not any("生成分析报告" in item.label for item in app.button)


def test_material_reports_are_selected_per_document(tmp_path, monkeypatch):
    data_dir = tmp_path / "seeded"
    service = MaterialService(data_dir)
    first = service.import_document("biology.txt", "细胞由细胞膜和细胞质构成。".encode()).material
    second = service.import_document("history.md", "秦朝统一文字和度量衡。".encode()).material
    source_first = service.get_sources(first.id)[0]
    source_second = service.get_sources(second.id)[0]
    service.save_analysis(first.id, _report("生物笔记", source_first.id))
    service.save_analysis(second.id, _report("历史笔记", source_second.id))

    app = _app(monkeypatch, data_dir)
    assert not app.exception
    app.selectbox(key="material_selector").select(first.id).run()
    assert any("生物笔记" in item.value for item in app.markdown)
    assert any("细胞由细胞膜" in item.value for item in app.text)
    app.selectbox(key="material_selector").select(second.id).run()
    assert not app.exception
    assert any("历史笔记" in item.value for item in app.markdown)
    assert any("秦朝统一文字" in item.value for item in app.text)
    assert not any("生物笔记" in item.value for item in app.markdown)
    assert any("S1" in item.value for item in app.markdown)


def test_upload_import_selects_new_material_instead_of_previous(tmp_path, monkeypatch):
    data_dir = tmp_path / "upload"
    service = MaterialService(data_dir)
    old = service.import_document("old-notes.txt", b"previously selected source").material
    app = _app(monkeypatch, data_dir)
    assert app.selectbox(key="material_selector").value == old.id
    app.file_uploader(key="material_upload").upload("new-notes.txt", b"newly uploaded material", "text/plain")
    app.run()
    app.button(key="import_material").click().run()
    assert not app.exception
    new_material = next(item for item in service.list_materials() if item.filename == "new-notes.txt")
    assert app.selectbox(key="material_selector").value == new_material.id
    assert any("newly uploaded material" in item.value for item in app.text)


@pytest.mark.parametrize("seed_count", [1, 2])
def test_deleting_selected_material_handles_last_and_remaining_items(tmp_path, monkeypatch, seed_count):
    data_dir = tmp_path / f"delete-{seed_count}"
    service = MaterialService(data_dir)
    for index in range(seed_count):
        service.import_document(f"note-{index}.txt", f"source {index}".encode())
    app = _app(monkeypatch, data_dir)
    selected = app.selectbox(key="material_selector").value
    app.button(key="delete_material").click().run()
    assert not app.exception
    remaining = service.list_materials()
    assert len(remaining) == seed_count - 1
    if remaining:
        assert app.selectbox(key="material_selector").value == remaining[0].id
    else:
        assert not app.selectbox


def test_secret_is_not_rendered_or_exported_and_missing_settings_disable_request(tmp_path, monkeypatch):
    data_dir = tmp_path / "secret-check"
    service = MaterialService(data_dir)
    material = service.import_document("notes.txt", "local-only text".encode()).material
    source = service.get_sources(material.id)[0]
    report = _report("安全报告", source.id)
    report["warnings"] = ["请核对资料原文"]
    service.save_analysis(material.id, report)
    secret = "test-secret-must-not-leak"
    monkeypatch.setenv("CSA_ANALYSIS_API_KEY", secret)
    captured_exports = {}
    original_download_button = st.download_button
    def capture_download(label, data, *args, **kwargs):
        captured_exports[label] = data
        return original_download_button(label, data, *args, **kwargs)
    monkeypatch.setattr(st, "download_button", capture_download)
    app = _app(monkeypatch, data_dir)
    assert not app.exception
    assert app.button(key="analyze_material").disabled
    visible = "\n".join(item.value for item in [*app.markdown, *app.caption, *app.text, *app.warning, *app.info])
    assert secret not in visible
    markdown_export = captured_exports["导出 Markdown"]
    json_export = captured_exports["导出 JSON"]
    json_content = json.loads(json_export)
    assert "S1" in markdown_export and "local-only text" in markdown_export
    assert "请核对资料原文" in markdown_export and "test-model" in markdown_export
    assert json_content["source_index"][0]["id"] == source.id
    assert json_content["source_index"][0]["text"] == source.text
    assert json_content["source_index"][0]["locator"] == source.locator
    assert json_content["report"]["metadata"]["model"] == "test-model"
    assert json_content["report"]["warnings"] == ["请核对资料原文"]
    assert secret not in markdown_export and secret not in json_export


def test_analysis_failure_keeps_previous_report_and_hides_unexpected_error(tmp_path, monkeypatch):
    data_dir = tmp_path / "failure"
    service = MaterialService(data_dir)
    material = service.import_document("notes.txt", b"prior source text").material
    source = service.get_sources(material.id)[0]
    old_report = _report("Existing report", source.id)
    service.save_analysis(material.id, old_report)
    monkeypatch.setattr(MaterialAnalyzer, "analyze", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("private-test-key leaked in cause")))

    app = _app(monkeypatch, data_dir)
    app.text_input(key="analysis_base_url").set_value("https://example.invalid/v1")
    app.text_input(key="analysis_model").set_value("test-model")
    app.text_input(key="analysis_api_key").set_value("private-test-key")
    app.run()
    app.button(key="analyze_material").click().run()
    assert not app.exception
    assert service.get_analysis(material.id) == old_report
    errors = "\n".join(item.value for item in app.error)
    assert "未预期错误" in errors
    assert "private-test-key" not in errors


def test_configured_analysis_uses_openai_compatible_client_and_saves_report(tmp_path, monkeypatch):
    data_dir = tmp_path / "mock-service"
    service = MaterialService(data_dir)
    material = service.import_document("lesson.txt", "Photosynthesis converts light energy.".encode()).material
    seen_requests = []

    def handler(request):
        seen_requests.append(request)
        request_payload = json.loads(request.content)
        prompt = request_payload["messages"][1]["content"]
        if "请为资料" in prompt:
            payload = {
                "title": "Photosynthesis lesson",
                "material_type": "讲义/文档",
                "summary": "Photosynthesis converts light energy into chemical energy.",
                "outline": [{"title": "Energy conversion", "summary": "Light energy is converted.", "source_ids": ["S1"], "children": []}],
                "knowledge_points": [{"name": "Photosynthesis", "explanation": "The process stores light energy as chemical energy.", "source_ids": ["S1"]}],
                "learning_suggestions": ["Review the steps in the process."],
                "warnings": [],
            }
        else:
            payload = {
                "coverage_source_ids": ["S1"],
                "summary": "The source describes photosynthesis.",
                "outline": [{"title": "Photosynthesis", "summary": "Light energy conversion.", "source_ids": ["S1"], "children": []}],
                "knowledge_points": [{"name": "Energy conversion", "explanation": "Light energy is stored chemically.", "source_ids": ["S1"]}],
            }
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]})

    def mock_http_init(self, config, client=None):
        self.config = config
        self._client = httpx.Client(transport=httpx.MockTransport(handler))
        self._owns_client = True

    monkeypatch.setattr("study_assistant.analysis.OnlineAnalysisClient.__init__", mock_http_init)
    app = _app(monkeypatch, data_dir)
    app.text_input(key="analysis_base_url").set_value("https://mock.example/v1")
    app.text_input(key="analysis_model").set_value("mock-model-id")
    app.text_input(key="analysis_api_key").set_value("integration-test-secret")
    app.run()
    app.button(key="analyze_material").click().run()

    saved = service.get_analysis(material.id)
    assert saved["title"] == "Photosynthesis lesson"
    assert saved["metadata"]["model"] == "mock-model-id"
    assert len(seen_requests) == 2
    assert all(request.url.path.endswith("/chat/completions") for request in seen_requests)
    assert all(request.headers["Authorization"] == "Bearer integration-test-secret" for request in seen_requests)
