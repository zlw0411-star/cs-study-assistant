"""Background jobs for the Qt desktop application.

All file parsing, database reads needed by analysis, and model calls run in a
worker thread. Analysis results are returned to the UI and are never persisted
here; the UI decides whether to call ``MaterialService.save_analysis``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .analysis import (
    MaterialAnalyzer,
    OnlineAnalysisClient,
    OnlineAnalysisError,
    OnlineModelConfig,
)
from .materials import MAX_FILE_BYTES, MaterialService


class _Cancelled(Exception):
    """Internal control flow for cooperative cancellation."""


class _CancellationClient:
    """Check the cancellation token around every potentially blocking call."""

    def __init__(self, client: OnlineAnalysisClient, cancel_event: threading.Event):
        self._client = client
        self._cancel_event = cancel_event
        self.config = client.config

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        if self._cancel_event.is_set():
            raise _Cancelled
        result = self._client.complete_json(system, user)
        if self._cancel_event.is_set():
            raise _Cancelled
        return result


class _JobWorker(QObject):
    progress = Signal(int, int, str)
    outcome_ready = Signal(str, object)

    def __init__(
        self,
        service: MaterialService,
        kind: str,
        cancel_event: threading.Event,
        path: str | None = None,
        material_id: str | None = None,
        config: OnlineModelConfig | None = None,
        client_factory: Callable[[OnlineModelConfig], OnlineAnalysisClient] = OnlineAnalysisClient,
    ) -> None:
        super().__init__()
        self.service = service
        self.kind = kind
        self.cancel_event = cancel_event
        self.path = path
        self.material_id = material_id
        self.config = config
        self.client_factory = client_factory
        self.outcome: tuple[str, Any] | None = None

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise _Cancelled

    def _progress(self, done: int, total: int, message: str) -> None:
        self._check_cancelled()
        self.progress.emit(done, total, message)
        self._check_cancelled()

    @Slot()
    def run(self) -> None:
        client: OnlineAnalysisClient | None = None
        try:
            self._check_cancelled()
            if self.kind == "import":
                if self.path is None:
                    raise RuntimeError("Missing import path")
                source_path = Path(self.path)
                if source_path.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError("文件大小不能超过 20 MB。")
                with source_path.open("rb") as source_file:
                    raw = source_file.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES:
                    raise ValueError("文件大小不能超过 20 MB。")
                self._check_cancelled()
                payload = self.service.import_document(source_path.name, raw)
            else:
                if self.material_id is None or self.config is None:
                    raise RuntimeError("Missing analysis parameters")
                material = self.service.get_material(self.material_id)
                self._check_cancelled()
                sources = self.service.get_sources(self.material_id)
                self._check_cancelled()
                client = self.client_factory(self.config)
                analyzer = MaterialAnalyzer(_CancellationClient(client, self.cancel_event))
                report = analyzer.analyze(material, sources, progress=self._progress)
                payload = {"material_id": self.material_id, "report": report}
            self._check_cancelled()
            self.outcome = ("succeeded", payload)
        except _Cancelled:
            self.outcome = ("cancelled", None)
        except Exception as exc:  # noqa: BLE001 - worker boundary must convert unknown errors safely
            if self.cancel_event.is_set():
                self.outcome = ("cancelled", None)
            else:
                self.outcome = ("failed", _safe_error(self.kind, exc))
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001 - preserve safe result despite cleanup errors
                    # Cleanup failures must not replace a safe job outcome.
                    if self.outcome is None or self.outcome[0] == "succeeded":
                        self.outcome = ("failed", _generic_error(self.kind))
            if self.cancel_event.is_set():
                self.outcome = ("cancelled", None)
            status, payload = self.outcome or ("failed", _generic_error(self.kind))
            self.outcome_ready.emit(status, payload)
            QThread.currentThread().quit()


class DesktopJobs(QObject):
    """Run one import or analysis task at a time on a managed QThread."""

    progress = Signal(int, int, str)
    succeeded = Signal(str, object)
    failed = Signal(str, str)
    cancelled = Signal(str)
    idle = Signal()

    def __init__(
        self,
        service: MaterialService,
        parent: QObject | None = None,
        client_factory: Callable[[OnlineModelConfig], OnlineAnalysisClient] = OnlineAnalysisClient,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.client_factory = client_factory
        self.busy = False
        self.job_kind: str | None = None
        self._thread: QThread | None = None
        self._worker: _JobWorker | None = None
        self._cancel_event: threading.Event | None = None
        self._outcome: tuple[str, Any] | None = None

    def start_import(self, path: Path | str) -> bool:
        if self.busy:
            return False
        return self._start("import", path=str(path))

    def start_analysis(self, material_id: str, config: OnlineModelConfig) -> bool:
        if self.busy:
            return False
        return self._start("analysis", material_id=material_id, config=config)

    def _start(
        self,
        kind: str,
        *,
        path: str | None = None,
        material_id: str | None = None,
        config: OnlineModelConfig | None = None,
    ) -> bool:
        self.busy = True
        self.job_kind = kind
        self._cancel_event = threading.Event()
        self._outcome = None
        thread = QThread(self)
        worker = _JobWorker(
            self.service,
            kind,
            self._cancel_event,
            path=path,
            material_id=material_id,
            config=config,
            client_factory=self.client_factory,
        )
        worker.moveToThread(thread)
        worker.progress.connect(self._forward_progress)
        worker.outcome_ready.connect(self._capture_outcome)
        thread.started.connect(worker.run)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()
        return True

    def cancel(self) -> None:
        if self.busy and self._cancel_event is not None:
            self._cancel_event.set()

    @Slot(int, int, str)
    def _forward_progress(self, done: int, total: int, message: str) -> None:
        if (
            self.busy
            and self._cancel_event is not None
            and not self._cancel_event.is_set()
        ):
            self.progress.emit(done, total, message)

    @Slot(str, object)
    def _capture_outcome(self, status: str, payload: Any) -> None:
        self._outcome = (status, payload)

    @Slot()
    def _on_thread_finished(self) -> None:
        # This slot runs in the controller's thread. The QThread has already
        # ended, so terminal signals and ownership cleanup are safe here.
        kind = self.job_kind
        token = self._cancel_event
        outcome = self._outcome
        was_cancelled = token is not None and token.is_set()

        self.busy = False
        self.job_kind = None
        self._thread = None
        self._worker = None
        self._cancel_event = None
        self._outcome = None

        if kind is not None:
            if was_cancelled or outcome is None or outcome[0] == "cancelled":
                self.cancelled.emit(kind)
            elif outcome[0] == "succeeded":
                self.succeeded.emit(kind, outcome[1])
            else:
                self.failed.emit(kind, str(outcome[1]))

        self.idle.emit()


def _generic_error(kind: str) -> str:
    return "资料导入失败，请检查文件后重试。" if kind == "import" else "资料分析失败，请检查模型设置或稍后重试。"


_SAFE_IMPORT_ERRORS = {
    "单个文件最多生成 300 个来源片段，请拆分文件后再导入。",
    "文件文字超过 600,000 个字符，请拆分文件后再导入。",
    "暂不支持带密码的 PDF 文件。",
    "PDF 中没有可读取的文字；当前不支持对扫描版 PDF 进行 OCR。",
    "PDF 文件损坏或无法读取，请检查文件后重试。",
    "DOCX 压缩包中的文件数量超过限制。",
    "DOCX 解压后超过 64 MB 限制。",
    "DOCX 压缩包包含不安全的路径。",
    "DOCX 文件无效：缺少正文内容。",
    "DOCX 文件损坏或无法读取，请检查文件后重试。",
    "DOCX 中没有可读取的文字。",
    "文件类型不受支持，请上传可选择文字的 PDF、DOCX、TXT 或 MD 文件。",
    "文件为空或没有可读取的文字。",
    "上传的文件为空。",
    "文件大小不能超过 20 MB。",
}


def _safe_error(kind: str, exc: Exception) -> str:
    if isinstance(exc, OnlineAnalysisError):
        message = str(exc).strip()
        if message:
            return message
    if isinstance(exc, ValueError):
        message = str(exc).strip()
        if (kind == "import" and message in _SAFE_IMPORT_ERRORS) or (
            kind == "analysis" and message == "找不到该资料。"
        ):
            return message
    return _generic_error(kind)
