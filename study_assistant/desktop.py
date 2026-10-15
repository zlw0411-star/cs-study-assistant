"""Native Qt desktop interface for local study material analysis."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QFrame,
    QStackedWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QSizePolicy,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .analysis import OnlineModelConfig
from .materials import MaterialService
from .desktop_theme import EmptyLibraryArt, apply_theme, make_icon


_ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self._full_text = text
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setFullText(self, text: str) -> None:
        self._full_text = text
        self.setText(text)
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.update()

    def minimumSizeHint(self):
        return self.fontMetrics().size(Qt.TextFlag.TextSingleLine, "…")

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        metrics = painter.fontMetrics()
        rect = self.contentsRect()
        painter.drawText(rect, self.alignment(), metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, rect.width()))


class ModelSettingsDialog(QDialog):
    """Session-only OpenAI-compatible service settings."""

    def __init__(self, base_url: str, model: str, api_key: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("在线模型设置")
        self.setObjectName("modelSettingsDialog")
        self.setMinimumWidth(440)
        apply_theme(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(18)
        form = QFormLayout()
        self.base_url_edit = QLineEdit(base_url)
        self.base_url_edit.setObjectName("settingsBaseUrl")
        self.base_url_edit.setPlaceholderText("https://api.example.com/v1")
        self.model_edit = QLineEdit(model)
        self.model_edit.setObjectName("settingsModel")
        self.model_edit.setPlaceholderText("服务商提供的模型 ID")
        self.api_key_edit = QLineEdit(api_key)
        self.api_key_edit.setObjectName("settingsApiKey")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("OpenAI 兼容服务地址", self.base_url_edit)
        form.addRow("模型 ID", self.model_edit)
        form.addRow("API 密钥", self.api_key_edit)
        layout.addLayout(form)
        notice = QLabel("密钥只保存在当前应用会话中，不写入资料库或导出文件。只有点击分析后，资料文字才会发送给该服务；较长资料可能分批产生多次请求和费用。")
        notice.setWordWrap(True)
        notice.setObjectName("settingsPrivacyNotice")
        layout.addWidget(notice)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def values(self) -> tuple[str, str, str]:
        return self.base_url_edit.text().strip(), self.model_edit.text().strip(), self.api_key_edit.text()


class MainWindow(QMainWindow):
    def __init__(self, service: MaterialService | None = None, jobs: Any = None):
        super().__init__()
        if jobs is None:
            from .desktop_jobs import DesktopJobs

            jobs = DesktopJobs(service or MaterialService(), parent=self)
        self.service = service or getattr(jobs, "service", None) or MaterialService()
        self.jobs = jobs
        self._closing = False
        self._cancel_requested = False
        self._material_id: str | None = None
        self._sources: list[Any] = []
        self._source_by_id: dict[str, Any] = {}
        self._report: dict[str, Any] | None = None
        self._base_url = os.getenv("CSA_ANALYSIS_BASE_URL", "")
        self._model = os.getenv("CSA_ANALYSIS_MODEL", "")
        self._api_key = os.getenv("CSA_ANALYSIS_API_KEY", "")
        self.setWindowTitle("学习资料分析助手")
        self.setObjectName("mainWindow")
        self.setMinimumSize(900, 600)
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(min(1280, max(900, available.width() - 72)), min(820, max(600, available.height() - 72)))
        else:
            self.resize(1200, 800)
        self.setAcceptDrops(True)
        apply_theme(self)
        self._build_ui()
        self._connect_jobs()
        self.refresh_library()
        self._update_controls()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("desktopRoot")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(236)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(16, 22, 16, 16)
        sidebar_layout.setSpacing(12)
        brand = QHBoxLayout()
        self.brand_icon = QLabel()
        self.brand_icon.setObjectName("brandIcon")
        self.brand_icon.setPixmap(make_icon("book", size=28).pixmap(28, 28))
        brand.addWidget(self.brand_icon, 0, Qt.AlignmentFlag.AlignTop)
        brand_words = QVBoxLayout()
        self.brand_title = QLabel("知页")
        self.brand_title.setObjectName("brandTitle")
        self.brand_subtitle = QLabel("学习资料助手")
        self.brand_subtitle.setObjectName("brandSubtitle")
        brand_words.addWidget(self.brand_title)
        brand_words.addWidget(self.brand_subtitle)
        brand.addLayout(brand_words, 1)
        sidebar_layout.addLayout(brand)

        library_heading = QHBoxLayout()
        self.library_title = QLabel("资料库")
        self.library_title.setObjectName("libraryTitle")
        self.library_count = QLabel("0 份")
        self.library_count.setObjectName("libraryCount")
        library_heading.addWidget(self.library_title)
        library_heading.addStretch(1)
        library_heading.addWidget(self.library_count)
        sidebar_layout.addLayout(library_heading)
        self.library_list = QListWidget()
        self.library_list.setObjectName("materialList")
        self.library_list.setSpacing(7)
        self.library_list.setFrameShape(QFrame.Shape.NoFrame)
        self.library_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.library_list.currentItemChanged.connect(self._on_material_changed)
        sidebar_layout.addWidget(self.library_list, 1)

        self.settings_action = QAction("模型设置", self)
        self.settings_action.setObjectName("settingsAction")
        self.settings_action.triggered.connect(self.open_settings)
        self.addAction(self.settings_action)
        self.import_button = QPushButton("导入资料")
        self.import_button.setObjectName("importButton")
        self.import_button.setProperty("class", "primaryButton")
        self.import_button.setIcon(make_icon("upload", color="#F5F0E8", size=18))
        self.import_button.clicked.connect(self.choose_import)
        sidebar_layout.addWidget(self.import_button)
        self.model_settings_button = QPushButton("模型设置")
        self.model_settings_button.setObjectName("modelSettingsButton")
        self.model_settings_button.setProperty("class", "toolButton")
        self.model_settings_button.setIcon(make_icon("settings", color="#D8DDE3", size=18))
        self.model_settings_button.clicked.connect(self.settings_action.trigger)
        sidebar_layout.addWidget(self.model_settings_button)
        self.delete_button = QPushButton("删除所选资料")
        self.delete_button.setObjectName("deleteMaterialButton")
        self.delete_button.setProperty("class", "toolButton")
        self.delete_button.setIcon(make_icon("trash", color="#D8DDE3", size=17))
        self.delete_button.clicked.connect(self.delete_selected)
        sidebar_layout.addWidget(self.delete_button)
        root_layout.addWidget(self.sidebar)

        self.content_area = QWidget()
        self.content_area.setObjectName("contentArea")
        content_layout = QVBoxLayout(self.content_area)
        content_layout.setContentsMargins(24, 18, 24, 18)
        content_layout.setSpacing(16)

        header = QFrame()
        header.setObjectName("mainToolbar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 12)
        header_layout.setSpacing(12)
        title_block = QVBoxLayout()
        self.file_title_label = ElidedLabel("挑选一份资料开始阅读")
        self.file_title_label.setObjectName("fileTitleLabel")
        self.file_title_label.setMinimumWidth(100)
        self.file_title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.file_stats_label = QLabel("PDF · DOCX · TXT · MD")
        self.file_stats_label.setObjectName("fileStatsLabel")
        self.file_stats_label.setWordWrap(True)
        title_block.addWidget(self.file_title_label)
        title_block.addWidget(self.file_stats_label)
        header_layout.addLayout(title_block, 1)

        self.analyze_button = QPushButton("生成分析")
        self.analyze_button.setObjectName("analyzeButton")
        self.analyze_button.setProperty("class", "primaryButton")
        self.analyze_button.setIcon(make_icon("sparkle", color="#FFFEFB", size=18))
        self.analyze_button.clicked.connect(self.start_analysis)
        header_layout.addWidget(self.analyze_button)
        self.cancel_button = QPushButton("停止")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setProperty("class", "toolButton")
        self.cancel_button.setIcon(make_icon("stop", size=16))
        self.cancel_button.clicked.connect(self.cancel_job)
        self.cancel_button.hide()
        header_layout.addWidget(self.cancel_button)
        self.export_md_button = QPushButton("Markdown")
        self.export_md_button.setObjectName("exportMarkdownButton")
        self.export_md_button.setProperty("class", "toolButton")
        self.export_md_button.setIcon(make_icon("download", size=16))
        self.export_md_button.setToolTip("导出 Markdown 分析报告")
        self.export_md_button.clicked.connect(lambda: self._choose_export("md"))
        header_layout.addWidget(self.export_md_button)
        self.export_json_button = QPushButton("JSON")
        self.export_json_button.setObjectName("exportJsonButton")
        self.export_json_button.setProperty("class", "toolButton")
        self.export_json_button.setIcon(make_icon("download", size=16))
        self.export_json_button.setToolTip("导出 JSON 分析报告")
        self.export_json_button.clicked.connect(lambda: self._choose_export("json"))
        header_layout.addWidget(self.export_json_button)
        content_layout.addWidget(header)

        self.body_stack = QStackedWidget()
        self.body_stack.setObjectName("bodyStack")
        self.empty_page = QWidget()
        self.empty_page.setObjectName("emptyLibraryPage")
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(24, 18, 24, 24)
        empty_layout.addStretch(1)
        self.empty_art = EmptyLibraryArt()
        self.empty_art.setObjectName("emptyLibraryArt")
        empty_layout.addWidget(self.empty_art, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_title = QLabel("把资料，读成自己的思路")
        self.empty_title.setObjectName("emptyLibraryTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        self.empty_copy = QLabel("从一份课程笔记开始。原文留在本机；你决定何时把文字发送给在线模型。")
        self.empty_copy.setObjectName("emptyLibraryCopy")
        self.empty_copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_copy.setWordWrap(True)
        empty_layout.addWidget(self.empty_copy)
        self.empty_import_button = QPushButton("导入第一份资料")
        self.empty_import_button.setObjectName("emptyImportButton")
        self.empty_import_button.setProperty("class", "primaryButton")
        self.empty_import_button.setIcon(make_icon("upload", color="#FFFEFB", size=18))
        self.empty_import_button.clicked.connect(self.choose_import)
        empty_layout.addWidget(self.empty_import_button, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)
        self.body_stack.addWidget(self.empty_page)

        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter.setObjectName("mainSplitter")
        self.workspace_splitter.setHandleWidth(16)
        source_panel = QFrame()
        source_panel.setObjectName("contentPanel")
        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(18, 16, 18, 16)
        source_layout.setSpacing(11)
        source_header = QHBoxLayout()
        source_title = QLabel("原文与来源")
        source_title.setObjectName("sectionTitle")
        self.source_hint = QLabel("点击来源跳转")
        self.source_hint.setWordWrap(False)
        self.source_hint.setMinimumWidth(80)
        self.source_hint.setMaximumWidth(100)
        source_hint_font = self.source_hint.font()
        source_hint_font.setPointSize(9)
        self.source_hint.setFont(source_hint_font)
        self.source_hint.setObjectName("mutedLabel")
        source_header.addWidget(source_title)
        source_header.addStretch(1)
        source_header.addWidget(self.source_hint)
        source_layout.addLayout(source_header)
        self.material_stats = QLabel("尚未选择资料")
        self.material_stats.setObjectName("materialStats")
        self.material_stats.setWordWrap(True)
        self.material_stats.setProperty("class", "mutedLabel")
        self.material_stats.hide()
        self.warning_label = QLabel("")
        self.warning_label.setObjectName("materialWarnings")
        self.warning_label.setWordWrap(True)
        self.warning_label.setProperty("class", "warningBanner")
        self.warning_label.setMaximumHeight(72)
        source_layout.addWidget(self.warning_label)
        self.source_list = QListWidget()
        self.source_list.setObjectName("sourceList")
        self.source_list.setMaximumHeight(48)
        self.source_list.setMinimumHeight(40)
        self.source_list.setSpacing(2)
        self.source_list.currentItemChanged.connect(self._on_source_changed)
        source_layout.addWidget(self.source_list, 0)
        self.source_preview = QPlainTextEdit()
        self.source_preview.setObjectName("sourcePreview")
        self.source_preview.setReadOnly(True)
        self.source_preview.document().setDefaultFont(self.font())
        self.source_preview.setPlaceholderText("选择一个来源片段以查看原文。")
        source_layout.addWidget(self.source_preview, 1)
        self.workspace_splitter.addWidget(source_panel)

        report_panel = QFrame()
        report_panel.setObjectName("contentPanel")
        report_layout = QVBoxLayout(report_panel)
        report_layout.setContentsMargins(18, 16, 18, 16)
        report_layout.setSpacing(11)
        report_title = QLabel("分析报告")
        report_title.setObjectName("sectionTitle")
        report_layout.addWidget(report_title)
        self.report_tabs = QTabWidget()
        self.report_tabs.setObjectName("reportTabs")
        self.report_meta_label = QLabel("")
        self.report_meta_label.setObjectName("reportMetaLabel")
        self.report_meta_label.setWordWrap(True)
        self.report_meta_label.setProperty("class", "mutedLabel")
        report_layout.addWidget(self.report_meta_label)
        self.report_views: dict[str, QTextBrowser] = {}
        for key, label in (("summary", "摘要"), ("outline", "大纲"), ("knowledge", "知识点"), ("suggestions", "建议")):
            view = QTextBrowser()
            view.setObjectName(f"report{key.title()}View")
            view.setOpenLinks(False)
            view.setOpenExternalLinks(False)
            view.document().setDefaultFont(self.font())
            view.document().setDefaultStyleSheet("body { font-size:14px; line-height:1.65; } p { margin:0 0 12px 0; } li { margin:0 0 9px 0; } h2 { margin:0 0 10px 0; } a { color:#AA4E2A; text-decoration:none; font-weight:600; } .sourceRefs { color:#657080; font-size:12px; }")
            view.anchorClicked.connect(self._on_report_source_clicked)
            self.report_views[key] = view
            self.report_tabs.addTab(view, label)
        report_layout.addWidget(self.report_tabs, 1)
        report_panel.setMinimumWidth(320)
        source_panel.setMinimumWidth(280)
        self.workspace_splitter.addWidget(report_panel)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.setSizes([430, 610])
        self.body_stack.addWidget(self.workspace_splitter)
        content_layout.addWidget(self.body_stack, 1)
        self.status_label = QLabel("导入资料后，可在本机查看来源并按需分析。")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("class", "mutedLabel")
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("jobProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.hide()
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.progress_bar, 0)
        content_layout.addLayout(status_row)
        root_layout.addWidget(self.content_area, 1)

        self._show_empty_state()

    def _make_material_card(self, material) -> tuple[Any, QFrame]:
        from PySide6.QtCore import QSize
        from PySide6.QtWidgets import QListWidgetItem

        item = QListWidgetItem(material.filename)
        item.setData(Qt.ItemDataRole.UserRole, material.id)
        item.setToolTip(material.filename)
        item.setSizeHint(QSize(0, 64))
        card = QFrame()
        card.setObjectName("libraryCard")
        card.setProperty("active", False)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(9, 7, 9, 7)
        card_layout.setSpacing(9)
        icon_label = QLabel()
        icon_label.setObjectName("libraryFileIcon")
        icon_name = "pdf" if material.kind == "pdf" else "text" if material.kind in {"txt", "md"} else "file"
        icon_label.setPixmap(make_icon(icon_name, size=22).pixmap(22, 22))
        icon_label.setToolTip(material.kind.upper())
        card_layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(3)
        filename = ElidedLabel(material.filename)
        filename.setObjectName("libraryFilename")
        filename.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        stats = QLabel(f"{material.character_count:,} 字  ·  {material.source_count} 段")
        stats.setObjectName("libraryFileStats")
        stats.setProperty("class", "mutedLabel")
        words.addWidget(filename)
        words.addWidget(stats)
        card_layout.addLayout(words, 1)
        return item, card

    @staticmethod
    def _new_source_item(label: str, source_id: str):
        from PySide6.QtWidgets import QListWidgetItem

        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, source_id)
        return item

    def _connect_jobs(self) -> None:
        self.jobs.progress.connect(self._on_progress)
        self.jobs.succeeded.connect(self._on_job_succeeded)
        self.jobs.failed.connect(self._on_job_failed)
        self.jobs.cancelled.connect(self._on_job_cancelled)
        self.jobs.idle.connect(self._on_jobs_idle)

    def refresh_library(self, select_id: str | None = None) -> None:
        initial_status = self.status_label.text() == "导入资料后，可在本机查看来源并按需分析。"
        try:
            items = self.service.list_materials()
        except sqlite3.Error:
            self.status_label.setText("无法读取本机资料库，请检查数据库文件状态后重试。")
            self._update_controls()
            return
        target = select_id or self._material_id
        self.library_list.blockSignals(True)
        self.library_list.clear()
        selected_row = -1
        self.library_count.setText(f"{len(items)} 份")
        for row, material in enumerate(items):
            item, card = self._make_material_card(material)
            self.library_list.addItem(item)
            self.library_list.setItemWidget(item, card)
            if material.id == target:
                selected_row = row
        if selected_row < 0 and items:
            selected_row = 0
        self.library_list.setCurrentRow(selected_row)
        self.library_list.blockSignals(False)
        self._sync_card_selection()
        if selected_row >= 0:
            self.body_stack.setCurrentWidget(self.workspace_splitter)
            self._load_material(items[selected_row].id)
            if initial_status:
                self._set_reading_status()
        else:
            self._material_id = None
            self._sources = []
            self._source_by_id = {}
            self._report = None
            self._show_empty_state()
        self._update_controls()

    def _show_empty_state(self) -> None:
        self.material_stats.setText("资料库为空。点击“导入资料…”或将单个 PDF、DOCX、TXT、MD 文件拖到窗口。")
        self.warning_label.clear()
        self.warning_label.hide()
        self.source_list.clear()
        self.source_preview.clear()
        self.file_title_label.setFullText("挑选一份资料开始阅读")
        self.file_stats_label.setText("PDF · DOCX · TXT · MD")
        self.body_stack.setCurrentWidget(self.empty_page)
        for view in self.report_views.values():
            view.setHtml("<p>导入支持格式的文字资料后，可以核对来源原文并生成摘要、提纲、知识点和学习建议。</p>")
        self._update_controls()

    def _on_material_changed(self, current, previous) -> None:
        if current is None:
            return
        self._sync_card_selection()
        material_id = current.data(Qt.ItemDataRole.UserRole)
        if material_id:
            self._load_material(str(material_id))
            if not self._is_busy():
                self._set_reading_status()

    def _sync_card_selection(self) -> None:
        current = self.library_list.currentItem()
        for row in range(self.library_list.count()):
            item = self.library_list.item(row)
            card = self.library_list.itemWidget(item)
            if card is not None:
                active = item is current
                if card.property("active") != active:
                    card.setProperty("active", active)
                    card.style().unpolish(card)
                    card.style().polish(card)

    def _set_reading_status(self) -> None:
        if self._report:
            self.status_label.setText("已载入这份资料的本地分析报告，可按来源核对原文。")
        else:
            self.status_label.setText("已载入本地资料，可选择来源查看原文或按需生成分析。")

    def _load_material(self, material_id: str) -> None:
        try:
            material = self.service.get_material(material_id)
            self._material_id = material_id
            self._sources = self.service.get_sources(material_id)
            self._source_by_id = {source.id: source for source in self._sources}
            self._report = self.service.get_analysis(material_id)
        except (ValueError, OSError, sqlite3.Error):
            self.status_label.setText("无法读取所选资料，请检查本机资料库后重试。")
            return
        page_count = len({source.page for source in self._sources if source.page is not None})
        paragraph_count = len({source.paragraph for source in self._sources if source.page is None and source.paragraph is not None})
        location_stats = f"{page_count} 页" if page_count else f"{paragraph_count} 个段落起点"
        self.material_stats.setText(f"{material.filename} · {material.kind.upper()}\n{material.character_count:,} 字 · {material.source_count} 个来源片段 · {location_stats}")
        self.file_title_label.setFullText(material.filename)
        self.file_stats_label.setText(f"{material.character_count:,} 字  ·  {material.source_count} 个来源片段  ·  {location_stats}")
        self.warning_label.setText("\n".join(material.warnings))
        self.warning_label.setVisible(bool(material.warnings))
        self.source_list.blockSignals(True)
        self.source_list.clear()
        for source in self._sources:
            self.source_list.addItem(self._new_source_item(f"{source.id}  ·  {source.locator}", source.id))
        self.source_list.setMaximumHeight(min(150, max(48, len(self._sources) * 44 + 8)))
        self.source_list.setCurrentRow(0 if self._sources else -1)
        self.source_list.blockSignals(False)
        self._on_source_changed(self.source_list.currentItem(), None)
        self._render_report(self._report)
        self.body_stack.setCurrentWidget(self.workspace_splitter)
        self._update_controls()

    def _on_source_changed(self, current, previous) -> None:
        if current is None:
            self.source_preview.clear()
            return
        source_id = current.data(Qt.ItemDataRole.UserRole)
        source = self._source_by_id.get(source_id)
        self.source_preview.setPlainText(source.text if source else "")

    def _source_link(self, source_ids: list[str]) -> str:
        links = []
        for source_id in source_ids:
            safe_id = html.escape(str(source_id), quote=True)
            links.append(f'<a href="source:{safe_id}">[{safe_id}]</a>')
        return " ".join(links)

    def _on_report_source_clicked(self, url: QUrl) -> None:
        source_id = url.toString().removeprefix("source:")
        for row in range(self.source_list.count()):
            if self.source_list.item(row).data(Qt.ItemDataRole.UserRole) == source_id:
                self.source_list.setCurrentRow(row)
                return

    def _render_report(self, report: dict[str, Any] | None) -> None:
        if not report:
            self.report_meta_label.clear()
            prompts = {
                "summary": "<h2>为这份资料生成整理报告</h2><p>填写你选择的 OpenAI 兼容服务配置，再点右上角“生成分析报告”。只有点击后才会发送文字。</p><p>分析会处理本文件中全部可提取文字，长资料可能分批多次请求并产生费用。</p>",
                "outline": "<h2>结构提纲</h2><p>分析完成后，这里会按章节展示资料脉络。每个条目都可以跳回来源原文核对。</p>",
                "knowledge": "<h2>知识点</h2><p>分析完成后，这里会列出概念说明及其原文出处。AI 生成内容需要自行核对。</p>",
                "suggestions": "<h2>学习建议</h2><p>分析完成后会根据资料内容生成复习建议。建议仅供参考，请结合课程目标判断。</p>",
            }
            for key, view in self.report_views.items():
                view.setHtml(prompts[key])
            return
        title = html.escape(str(report.get("title") or "分析报告"))
        summary_raw = str(report.get("summary", ""))
        summary = "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in summary_raw.splitlines() if paragraph.strip())
        meta = report.get("metadata", {})
        analyzed = html.escape(str(meta.get("analyzed_source_count", "?")))
        total = html.escape(str(meta.get("total_source_count", "?")))
        model_name = html.escape(str(meta.get("model", "未知")))
        self.report_meta_label.setText(f"来源覆盖 {analyzed}/{total} 段  ·  {model_name}")
        warnings = report.get("warnings", [])
        warning_html = "".join(f"<li>{html.escape(str(warning))}</li>" for warning in warnings)
        warning_section = f"<aside><h3>需要留意</h3><ul>{warning_html}</ul></aside>" if warnings else ""
        self.report_views["summary"].setHtml(f"<h2>{title}</h2>{summary}{warning_section}<p><b>AI 生成内容，请自行核对来源和结论。</b></p>")

        def outline_html(nodes: list[dict[str, Any]], depth: int = 0) -> str:
            chunks = []
            for node in nodes:
                chunks.append("<li><h3>" + html.escape(str(node.get("title", ""))) + "</h3><p>" + html.escape(str(node.get("summary", ""))) + "</p><p class='sourceRefs'>来源：" + self._source_link(node.get("source_ids", [])) + "</p>" + outline_html(node.get("children", []), depth + 1) + "</li>")
            return "<ol>" + "".join(chunks) + "</ol>" if chunks else ""
        outline = outline_html(report.get("outline", []))
        self.report_views["outline"].setHtml(outline or "<p>暂无提纲。</p>")
        points = "".join(f"<li><h3>{html.escape(str(point.get('name', '')))}</h3><p>{html.escape(str(point.get('explanation', '')))}</p><p class='sourceRefs'>来源：{self._source_link(point.get('source_ids', []))}</p></li>" for point in report.get("knowledge_points", []))
        self.report_views["knowledge"].setHtml(f"<ol>{points}</ol><p><b>AI 生成内容，请自行核对来源和结论。</b></p>")
        suggestions = "".join(f"<li><p>{html.escape(str(item))}</p></li>" for item in report.get("learning_suggestions", []))
        self.report_views["suggestions"].setHtml(f"<ol>{suggestions}</ol><p><b>AI 生成内容，请自行核对来源和结论。</b></p>")

    def choose_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择学习资料", "", "学习资料 (*.pdf *.docx *.txt *.md)")
        if path:
            self._start_import_path(path)

    def _start_import_path(self, path: str | Path) -> bool:
        file_path = Path(path)
        if self._closing or self._is_busy():
            return False
        if file_path.suffix.casefold() not in _ALLOWED_SUFFIXES:
            QMessageBox.information(self, "文件格式不支持", "请选择可提取文字的 PDF、DOCX、TXT 或 MD 文件。")
            return False
        self.status_label.setText("正在导入并提取资料文字…")
        started = bool(self.jobs.start_import(str(file_path)))
        if started:
            self._update_controls()
        else:
            self.status_label.setText("当前有任务正在运行，请稍候。")
        return started

    def delete_selected(self) -> None:
        if self._closing or self._is_busy() or not self._material_id:
            return
        answer = QMessageBox.question(self, "删除资料", "删除所选资料及其分析报告？此操作无法撤销。")
        if answer != QMessageBox.StandardButton.Yes:
            return
        material_id = self._material_id
        try:
            self.service.delete_material(material_id)
        except sqlite3.Error:
            self.status_label.setText("删除失败，无法更新本机资料库；所选资料仍保留。")
            return
        self.refresh_library()

    def open_settings(self) -> None:
        if self._closing or self._is_busy():
            return
        dialog = ModelSettingsDialog(self._base_url, self._model, self._api_key, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._base_url, self._model, self._api_key = dialog.values()
            self.status_label.setText("模型设置仅保存在当前应用会话中。未点击分析前不会发送资料。")
            self._update_controls()

    def _model_config(self) -> OnlineModelConfig | None:
        if not (self._base_url.strip() and self._model.strip() and self._api_key.strip()):
            return None
        try:
            return OnlineModelConfig(self._base_url, self._model, self._api_key)
        except ValueError as exc:
            QMessageBox.warning(self, "模型设置无效", str(exc))
            return None

    def start_analysis(self) -> None:
        if self._closing or self._is_busy() or not self._material_id:
            return
        config = self._model_config()
        if config is None:
            QMessageBox.information(self, "需要配置模型", "请先打开“模型设置”，填写服务地址、模型 ID 和 API 密钥。")
            return
        self.status_label.setText("分析时将把所选资料的全部可提取文字分批发送到配置的服务，可能产生多次请求和费用。")
        if self.jobs.start_analysis(self._material_id, config):
            self._update_controls()
        else:
            self.status_label.setText("当前有任务正在运行，请稍候。")

    def cancel_job(self) -> None:
        if self._closing or not self._is_busy():
            return
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self.status_label.setText("正在停止，等待当前请求结束…")
        self.jobs.cancel()
        self._update_controls()

    def _is_busy(self) -> bool:
        return bool(getattr(self.jobs, "busy", False))

    def _update_controls(self) -> None:
        busy = self._is_busy()
        has_material = self._material_id is not None
        accepting = not self._closing
        for widget in (self.library_list, self.import_button, self.empty_import_button, self.model_settings_button, self.delete_button, self.settings_action):
            widget.setEnabled(not busy and accepting)
        self.delete_button.setEnabled(has_material and not busy and accepting)
        self.analyze_button.setEnabled(has_material and not busy and accepting)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy and not self._cancel_requested and accepting)
        self.export_md_button.setEnabled(bool(self._report) and not busy and accepting)
        self.export_json_button.setEnabled(bool(self._report) and not busy and accepting)
        if busy:
            self.progress_bar.show()
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.hide()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if self._closing or self._cancel_requested:
            return
        self.status_label.setText(message)
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(min(done, total))

    def _on_job_succeeded(self, kind: str, payload: object) -> None:
        if self._closing:
            return
        if kind == "import":
            result = payload
            material = result.material
            self.refresh_library(select_id=material.id)
            if result.duplicate:
                self.status_label.setText("该文件已存在，已切换到原有资料。")
            else:
                self.status_label.setText("资料已导入到本机。")
            if result.warnings:
                self.warning_label.setText("\n".join(result.warnings))
                self.warning_label.setVisible(True)
        elif kind == "analysis":
            material_id = payload.get("material_id")
            report = payload.get("report")
            if not isinstance(report, dict) or material_id != self._material_id:
                return
            try:
                self.service.save_analysis(material_id, report)
            except (ValueError, OSError, sqlite3.Error):
                self.status_label.setText("分析完成，但报告保存失败；请重试。")
                return
            self._report = report
            self._render_report(report)
            self.status_label.setText("分析完成，报告已按资料分别保存在本机。")
        self._update_controls()

    def _on_job_failed(self, kind: str, message: str) -> None:
        if self._closing:
            return
        self.status_label.setText(f"任务失败：{message or '发生未预期错误。'} 已有成功报告仍会保留。")
        self._update_controls()

    def _on_job_cancelled(self, kind: str) -> None:
        if not self._closing:
            self.status_label.setText("任务已停止。导入任务可能已在本机完成保存，资料库已刷新。")

    def _on_jobs_idle(self) -> None:
        # Import cancellation can finish local persistence before the worker is idle.
        self._cancel_requested = False
        self.refresh_library(select_id=self._material_id)
        self._update_controls()
        if self._closing:
            QTimer.singleShot(0, self.close)

    def _choose_export(self, extension: str) -> None:
        if not self._report or not self._material_id:
            return
        try:
            material = self.service.get_material(self._material_id)
        except (ValueError, sqlite3.Error):
            QMessageBox.warning(self, "导出失败", "无法读取资料信息，报告未导出。")
            return
        suffix = ".md" if extension == "md" else ".json"
        path, _ = QFileDialog.getSaveFileName(self, "导出分析报告", f"{Path(material.filename).stem}-analysis{suffix}", "Markdown (*.md)" if extension == "md" else "JSON (*.json)")
        if path:
            self.export_report(path, extension)

    def _export_payload(self) -> dict[str, Any]:
        assert self._material_id and self._report
        material = self.service.get_material(self._material_id)
        sources = self.service.get_sources(self._material_id)
        return {
            "material": {"filename": material.filename, "kind": material.kind, "imported_at": material.imported_at},
            "report": self._report,
            "source_index": [{"id": source.id, "locator": source.locator, "page": source.page, "paragraph": source.paragraph, "text": source.text} for source in sources],
        }

    def export_report(self, path: str | Path, extension: str | None = None) -> None:
        if not self._report or not self._material_id:
            return
        suffix = (extension or Path(path).suffix.lower().lstrip(".")).lower()
        if suffix not in {"md", "json"}:
            suffix = "md"
        destination = Path(path)
        if destination.suffix.lower() != f".{suffix}":
            destination = destination.with_suffix(f".{suffix}") if destination.suffix else destination.with_name(destination.name + f".{suffix}")
        try:
            payload = self._export_payload()
            if suffix == "json":
                content = json.dumps(payload, ensure_ascii=False, indent=2)
            else:
                content = self._markdown_export(payload)
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as stream:
                    temporary_path = Path(stream.name)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary_path.replace(destination)
            finally:
                if temporary_path is not None and temporary_path.exists():
                    temporary_path.unlink()
        except (OSError, sqlite3.Error, ValueError):
            QMessageBox.warning(self, "导出失败", "无法写入目标文件，请检查目录权限和磁盘空间；原有导出文件未被替换。")
            self.status_label.setText("报告导出失败；原有目标文件仍保留。")
            return
        self.status_label.setText(f"报告已导出：{destination}")

    @staticmethod
    def _markdown_export(payload: dict[str, Any]) -> str:
        report = payload["report"]
        metadata = report.get("metadata", {})
        lines = [f"# {report.get('title') or payload['material']['filename']}", "", f"资料文件：{payload['material']['filename']}", "", "AI 生成内容，请结合来源原文核对。", "", "## 摘要", "", str(report.get("summary", "")), "", "## 结构提纲"]

        def write_outline(nodes: list[dict[str, Any]], depth: int = 0) -> None:
            for node in nodes:
                refs = ", ".join(node.get("source_ids", []))
                lines.append(f"{'  ' * depth}- **{node.get('title', '')}**：{node.get('summary', '')}（来源：{refs}）")
                write_outline(node.get("children", []), depth + 1)
        write_outline(report.get("outline", []))
        lines += ["", "## 知识点"]
        lines.extend(f"- **{point.get('name', '')}**：{point.get('explanation', '')}（来源：{', '.join(point.get('source_ids', []))}）" for point in report.get("knowledge_points", []))
        lines += ["", "## 学习建议"]
        lines.extend(f"- {item}" for item in report.get("learning_suggestions", []))
        lines += ["", "## 分析信息", f"- 模型：{metadata.get('model', '未知')}", f"- 生成时间：{metadata.get('generated_at', '未知')}", f"- 来源覆盖：{metadata.get('analyzed_source_count', '?')}/{metadata.get('total_source_count', '?')} 段", "", "## 分析提示"]
        lines.extend(f"- {warning}" for warning in report.get("warnings", []))
        lines += ["", "## 来源索引"]
        lines.extend(f"- **{source['id']}**（{source['locator']}）：{source['text']}" for source in payload["source_index"])
        return "\n".join(lines)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._closing or self._is_busy():
            event.ignore()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if self._closing or self._is_busy():
            event.ignore()
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if len(paths) != 1 or Path(paths[0]).is_dir():
            QMessageBox.information(self, "请选择单个文件", "一次拖入一个 PDF、DOCX、TXT 或 MD 文件。")
            event.ignore()
            return
        self._start_import_path(paths[0])
        event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        if self._is_busy():
            if not self._closing:
                self._closing = True
                self._cancel_requested = True
                self.status_label.setText("正在停止，等待当前请求结束后关闭窗口…")
                self._update_controls()
                self.jobs.cancel()
            event.ignore()
            return
        event.accept()


def main(argv: list[str] | None = None) -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv if argv is not None else sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
