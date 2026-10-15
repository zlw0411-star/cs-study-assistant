from __future__ import annotations

import json
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from study_assistant.analysis import (
    OnlineAnalysisClient,
    OnlineAnalysisError,
    OnlineModelConfig,
)
from study_assistant.desktop_jobs import DesktopJobs
from study_assistant.materials import MAX_FILE_BYTES, MaterialService


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def wait_for_idle(app: QApplication, jobs: DesktopJobs, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while jobs.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert not jobs.busy, "desktop job did not become idle before timeout"
    assert jobs.job_kind is None
    assert jobs._thread is None
    assert jobs._worker is None


def model_config() -> OnlineModelConfig:
    return OnlineModelConfig("https://example.test/v1", "mock-model", "test-key")


def map_json(ids: list[str]) -> dict:
    return {
        "coverage_source_ids": ids,
        "summary": "资料摘要",
        "outline": [{"title": "主题", "summary": "提纲摘要", "source_ids": ids, "children": []}],
        "knowledge_points": [{"name": "知识点", "explanation": "知识说明", "source_ids": ids}],
    }


def final_json(ids: list[str]) -> dict:
    return {
        "title": "资料报告",
        "material_type": "讲义/文档",
        "summary": "资料摘要",
        "outline": [{"title": "主题", "summary": "提纲摘要", "source_ids": ids, "children": []}],
        "knowledge_points": [{"name": "知识点", "explanation": "知识说明", "source_ids": ids}],
        "learning_suggestions": ["复习知识点并通过练习检验掌握情况。"],
        "warnings": [],
    }


def chat_response(value: dict) -> dict:
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value, ensure_ascii=False)}}]}


def import_text(service: MaterialService, filename: str = "notes.txt") -> str:
    imported = service.import_document(filename, b"First paragraph.\n\nSecond paragraph.")
    return imported.material.id


def test_import_runs_in_worker_and_emits_material_import_result(tmp_path, qapp):
    service = MaterialService(tmp_path / "data")
    file_path = tmp_path / "notes.txt"
    file_path.write_text("This is a study note.", encoding="utf-8")
    jobs = DesktopJobs(service)
    outcomes = []
    idle = []
    jobs.succeeded.connect(lambda kind, value: outcomes.append((kind, value)))
    jobs.idle.connect(lambda: idle.append(True))

    assert jobs.start_import(file_path)
    worker_destroyed = threading.Event()
    thread_destroyed = threading.Event()
    jobs._worker.destroyed.connect(lambda *_: worker_destroyed.set())
    jobs._thread.destroyed.connect(lambda *_: thread_destroyed.set())
    assert jobs.busy and jobs.job_kind == "import"
    wait_for_idle(qapp, jobs)
    cleanup_deadline = time.monotonic() + 2
    while (not worker_destroyed.is_set() or not thread_destroyed.is_set()) and time.monotonic() < cleanup_deadline:
        qapp.processEvents()
        time.sleep(0.005)

    assert len(outcomes) == 1
    kind, result = outcomes[0]
    assert kind == "import"
    assert result.material.filename == "notes.txt"
    assert result.material.source_count == 1
    assert len(service.list_materials()) == 1
    assert idle == [True]
    assert worker_destroyed.is_set()
    assert thread_destroyed.is_set()


def test_analysis_uses_real_analyzer_and_mocktransport_without_saving(tmp_path, qapp):
    service = MaterialService(tmp_path / "data")
    material_id = import_text(service)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "mock-model"
        user = payload["messages"][1]["content"]
        seen.append(user)
        if "期望覆盖ID：" in user:
            ids = json.loads(user.split("期望覆盖ID：", 1)[1].split("\n", 1)[0])
            result = map_json(ids)
        else:
            result = final_json(["S1"])
        return httpx.Response(200, json=chat_response(result))

    closed = threading.Event()

    class OwnedMockClient(OnlineAnalysisClient):
        def __init__(self, config):
            transport_client = httpx.Client(transport=httpx.MockTransport(handler))
            super().__init__(config, transport_client)
            self._owned_transport_client = transport_client

        def close(self):
            self._owned_transport_client.close()
            closed.set()

    jobs = DesktopJobs(service, client_factory=OwnedMockClient)
    outcomes = []
    jobs.succeeded.connect(lambda kind, value: outcomes.append((kind, value)))
    assert jobs.start_analysis(material_id, model_config())
    wait_for_idle(qapp, jobs)

    assert len(outcomes) == 1 and outcomes[0][0] == "analysis"
    payload = outcomes[0][1]
    assert payload["material_id"] == material_id
    assert payload["report"]["title"] == "资料报告"
    assert len(seen) == 2
    assert service.get_analysis(material_id) is None
    assert closed.is_set()


def test_busy_job_rejects_second_start(tmp_path, qapp):
    entered = threading.Event()
    release = threading.Event()

    class SlowImportService(MaterialService):
        def import_document(self, filename, raw):
            entered.set()
            assert release.wait(3)
            return super().import_document(filename, raw)

    service = SlowImportService(tmp_path / "data")
    file_path = tmp_path / "slow.txt"
    file_path.write_text("Slow import.", encoding="utf-8")
    jobs = DesktopJobs(service)
    assert jobs.start_import(file_path)
    assert entered.wait(2)
    assert jobs.start_import(file_path) is False
    assert jobs.start_analysis("missing", model_config()) is False
    assert jobs.job_kind == "import"
    release.set()
    wait_for_idle(qapp, jobs)


def test_oversized_file_is_rejected_before_reading_into_memory(tmp_path, qapp):
    service = MaterialService(tmp_path / "data")
    file_path = tmp_path / "large.txt"
    with file_path.open("wb") as stream:
        stream.truncate(MAX_FILE_BYTES + 1)
    jobs = DesktopJobs(service)
    failures = []
    jobs.failed.connect(lambda kind, message: failures.append((kind, message)))

    assert jobs.start_import(file_path)
    wait_for_idle(qapp, jobs)
    assert failures == [("import", "文件大小不能超过 20 MB。")]
    assert service.list_materials() == []


def test_cancel_waits_for_current_request_and_suppresses_followup_and_success(tmp_path, qapp):
    service = MaterialService(tmp_path / "data")
    material_id = import_text(service)
    request_started = threading.Event()
    release_request = threading.Event()
    closed = threading.Event()
    calls = []

    class BlockingClient:
        config = model_config()

        def complete_json(self, system, user):
            calls.append(user)
            request_started.set()
            assert release_request.wait(3)
            return map_json(["S1"])

        def close(self):
            closed.set()

    jobs = DesktopJobs(service, client_factory=lambda _config: BlockingClient())
    succeeded = []
    failures = []
    cancelled = []
    idles = []
    jobs.succeeded.connect(lambda *args: succeeded.append(args))
    jobs.failed.connect(lambda *args: failures.append(args))
    jobs.cancelled.connect(cancelled.append)
    jobs.idle.connect(lambda: idles.append(True))

    assert jobs.start_analysis(material_id, model_config())
    assert request_started.wait(2)
    jobs.cancel()
    assert jobs.busy  # No blocking wait or unsafe thread termination.
    release_request.set()
    wait_for_idle(qapp, jobs)

    assert len(calls) == 1
    assert succeeded == []
    assert failures == []
    assert cancelled == ["analysis"]
    assert idles == [True]
    assert closed.is_set()
    assert service.get_analysis(material_id) is None


def test_failure_preserves_old_report_and_closes_client(tmp_path, qapp):
    service = MaterialService(tmp_path / "data")
    material_id = import_text(service)
    previous = {"title": "previous report"}
    service.save_analysis(material_id, previous)
    closed = threading.Event()

    class FailingClient:
        config = model_config()

        def complete_json(self, system, user):
            raise OnlineAnalysisError("模型服务请求失败（HTTP 503）。")

        def close(self):
            closed.set()

    jobs = DesktopJobs(service, client_factory=lambda _config: FailingClient())
    failures = []
    succeeded = []
    jobs.failed.connect(lambda kind, message: failures.append((kind, message)))
    jobs.succeeded.connect(lambda *args: succeeded.append(args))

    assert jobs.start_analysis(material_id, model_config())
    wait_for_idle(qapp, jobs)

    assert failures == [("analysis", "模型服务请求失败（HTTP 503）。")]
    assert succeeded == []
    assert service.get_analysis(material_id) == previous
    assert closed.is_set()


def test_unknown_value_error_never_reaches_ui(tmp_path, qapp):
    service = MaterialService(tmp_path / "data")
    material_id = import_text(service)

    class SecretValueErrorClient:
        config = model_config()

        def complete_json(self, system, user):
            raise ValueError("private-secret-value")

        def close(self):
            pass

    jobs = DesktopJobs(service, client_factory=lambda _config: SecretValueErrorClient())
    failures = []
    jobs.failed.connect(lambda kind, message: failures.append((kind, message)))
    assert jobs.start_analysis(material_id, model_config())
    wait_for_idle(qapp, jobs)
    assert failures == [("analysis", "资料分析失败，请检查模型设置或稍后重试。")]
