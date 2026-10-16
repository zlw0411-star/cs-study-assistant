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
    QComboBox,
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
    QTreeWidget,
    QTreeWidgetItem,
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
        self._online_report: dict[str, Any] | None = None
        self._local_report: dict[str, Any] | None = None
        self._online_warning = ""
        self._material_modes: dict[str, str] = {}
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

        self.analyze_button = QPushButton("在线智能整理")
        self.analyze_button.setObjectName("analyzeButton")
        self.analyze_button.setProperty("class", "primaryButton")
        self.analyze_button.setIcon(make_icon("sparkle", color="#FFFEFB", size=18))
        self.analyze_button.setToolTip("发送本资料的全部提取文字到配置的模型服务；长资料可能分批多次请求并计费。")
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
        self.warning_label.setTextFormat(Qt.TextFormat.PlainText)
        self.warning_label.setWordWrap(True)
        self.warning_label.setProperty("class", "warningBanner")
        self.warning_label.setMaximumHeight(72)
        source_layout.addWidget(self.warning_label)
        self.warning_button = QPushButton("查看全部提取警告")
        self.warning_button.clicked.connect(self._show_extraction_warnings)
        self.warning_button.hide()
        source_layout.addWidget(self.warning_button)
        limitation = QLabel("仅核对提取文字：扫描件需 OCR；复杂公式、表格及版式可能丢失，请对照原文件。")
        limitation.setWordWrap(True)
        source_layout.addWidget(limitation)
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
        report_title = QLabel("整理结果")
        report_title.setObjectName("sectionTitle")
        report_heading = QHBoxLayout()
        report_heading.addWidget(report_title)
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("reportModeCombo")
        self.mode_combo.addItem("本地整理", "local")
        self.mode_combo.addItem("智能整理", "online")
        self.mode_combo.setToolTip("本地整理只识别标题与原文分段；智能整理需显式点击按钮调用模型。")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        report_heading.addWidget(self.mode_combo)
        report_layout.addLayout(report_heading)
        self.report_tabs = QTabWidget()
        self.report_tabs.setObjectName("reportTabs")
        self.report_meta_label = QLabel("")
        self.report_meta_label.setObjectName("reportMetaLabel")
        self.report_meta_label.setWordWrap(True)
        self.report_meta_label.setProperty("class", "mutedLabel")
        self.report_meta_label.setMaximumHeight(64)
        report_layout.addWidget(self.report_meta_label)
        self.report_details_button = QPushButton("查看整理详情")
        self.report_details_button.setObjectName("reportDetailsButton")
        self.report_details_button.clicked.connect(self._show_report_details)
        self.report_details_button.hide()
        report_layout.addWidget(self.report_details_button)
        self.report_views: dict[str, QTextBrowser] = {}
        self.outline_tree = QTreeWidget()
        self.outline_tree.setObjectName("outlineTree")
        self.outline_tree.setHeaderHidden(True)
        self.outline_tree.setMinimumHeight(40)
        self.outline_tree.setMaximumHeight(120)
        self.outline_tree.currentItemChanged.connect(self._on_outline_selected)
        self.outline_tree.itemClicked.connect(lambda item, column: self._on_outline_selected(item, None))
        for key, label in (("summary", "摘要"), ("outline", "大纲"), ("knowledge", "知识点"), ("suggestions", "建议")):
            view = QTextBrowser()
            view.setObjectName(f"report{key.title()}View")
            view.setOpenLinks(False)
            view.setOpenExternalLinks(False)
            view.document().setDefaultFont(self.font())
            view.document().setDefaultStyleSheet("body { font-size:14px; line-height:1.65; } p { margin:0 0 12px 0; } li { margin:0 0 9px 0; } h2 { margin:0 0 10px 0; } a { color:#AA4E2A; text-decoration:none; font-weight:600; } .sourceRefs { color:#657080; font-size:12px; }")
            view.anchorClicked.connect(self._on_report_source_clicked)
            self.report_views[key] = view
            if key == "outline":
                outline_page = QWidget()
                outline_layout = QVBoxLayout(outline_page)
                outline_layout.setContentsMargins(0, 0, 0, 0)
                outline_layout.addWidget(self.outline_tree)
                outline_layout.addWidget(view, 1)
                self.report_tabs.addTab(outline_page, label)
            else:
                self.report_tabs.addTab(view, label)
        report_layout.addWidget(self.report_tabs, 1)
        report_panel.setMinimumWidth(320)
        source_panel.setMinimumWidth(280)
        self.workspace_splitter.insertWidget(0, report_panel)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.setSizes([610, 430])
        self.body_stack.addWidget(self.workspace_splitter)
        content_layout.addWidget(self.body_stack, 1)
        self.online_notice_label = QLabel("在线智能整理发送本资料全部提取文字，可能多次计费；本地整理不联网。")
        self.online_notice_label.setObjectName("onlineAnalysisNotice")
        self.online_notice_label.setWordWrap(True)
        self.online_notice_label.setProperty("class", "mutedLabel")
        content_layout.addWidget(self.online_notice_label)
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
            loaded = self._load_material(items[selected_row].id)
            if initial_status and loaded:
                self._set_reading_status()
        else:
            self._material_id = None
            self._sources = []
            self._source_by_id = {}
            self._report = None
            self._online_report = None
            self._local_report = None
            self._show_empty_state()
        self._update_controls()

    def _show_empty_state(self) -> None:
        self.material_stats.setText("资料库为空。点击“导入资料…”或将单个 PDF、DOCX、TXT、MD 文件拖到窗口。")
        self.warning_label.clear()
        self.warning_label.hide()
        self.warning_button.hide()
        self.outline_tree.clear()
        self.report_meta_label.clear()
        self.report_details_button.hide()
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
            loaded = self._load_material(str(material_id))
            if loaded and not self._is_busy():
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
        if self.mode_combo.currentData() == "local":
            self.status_label.setText(self._online_warning or "已载入本地目录，可按来源核对原文；未调用模型。")
        elif self._report:
            self.status_label.setText("已载入智能整理报告，可按来源核对原文。")
        else:
            self.status_label.setText("尚未生成智能报告；切换模式不会联网，点击“在线智能整理”才会发送资料。")

    @staticmethod
    def _readable_online_report(report: Any) -> bool:
        """Tolerate sparse legacy reports, but reject shapes unsafe to render."""
        if not isinstance(report, dict) or not isinstance(report.get("metadata", {}), dict):
            return False
        if any(not isinstance(report.get(key, []), list) for key in
               ("outline", "knowledge_points", "warnings", "learning_suggestions")):
            return False
        def valid_refs(value):
            return isinstance(value, list) and all(isinstance(sid, str) for sid in value)

        if not valid_refs(report.get("metadata", {}).get("unreferenced_source_ids", [])):
            return False
        stack = [(node, 1) for node in report.get("outline", [])]
        visited = 0
        while stack:
            node, depth = stack.pop()
            visited += 1
            if (visited > 600 or depth > 32 or not isinstance(node, dict)
                    or not isinstance(node.get("children", []), list)
                    or not valid_refs(node.get("source_ids", []))):
                return False
            stack.extend((child, depth + 1) for child in node.get("children", []))
        return all(isinstance(point, dict) and isinstance(point.get("chapter", ""), str)
                   and valid_refs(point.get("source_ids", []))
                   for point in report.get("knowledge_points", []))

    def _load_material(self, material_id: str) -> bool:
        try:
            material = self.service.get_material(material_id)
            sources = self.service.get_sources(material_id)
            from .local_organization import build_local_report

            local_report = build_local_report(material, sources)
        except (TypeError, ValueError, OSError, sqlite3.Error):
            self._material_id = None
            self._sources = []
            self._source_by_id = {}
            self._report = None
            self._online_report = None
            self._local_report = None
            self._online_warning = ""
            self._show_empty_state()
            self.file_title_label.setFullText("资料读取失败")
            self.status_label.setText("无法读取所选资料，请检查本机资料库后重试。")
            self._update_controls()
            return False
        # A corrupt online report must not disable readable local sources.
        self._online_warning = ""
        try:
            report = self.service.get_analysis(material_id)
            if report is not None and not self._readable_online_report(report):
                raise ValueError("Stored online report has an invalid shape")
        except (TypeError, ValueError, OSError, sqlite3.Error, RecursionError):
            report = None
            self._online_warning = "智能报告读取失败，本地目录仍可使用；原有智能报告未被修改。"
            local_report["warnings"].append(self._online_warning)
            self._material_modes[material_id] = "local"
        self._material_id = material_id
        self._sources = sources
        self._source_by_id = {source.id: source for source in sources}
        self._online_report = report
        self._local_report = local_report
        mode = self._material_modes.get(material_id, "online" if report else "local")
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(mode))
        self.mode_combo.blockSignals(False)
        self._report = local_report if mode == "local" else report
        page_count = len({source.page for source in self._sources if source.page is not None})
        paragraph_count = len({source.paragraph for source in self._sources if source.page is None and source.paragraph is not None})
        location_stats = f"{page_count} 页" if page_count else f"{paragraph_count} 个段落起点"
        self.material_stats.setText(f"{material.filename} · {material.kind.upper()}\n{material.character_count:,} 字 · {material.source_count} 个来源片段 · {location_stats}")
        self.file_title_label.setFullText(material.filename)
        self.file_stats_label.setText(f"{material.kind.upper()} · {material.character_count:,} 字符 · {material.source_count} 个来源片段 · {location_stats}")
        self.warning_label.setText("\n".join(material.warnings))
        self.warning_label.setVisible(bool(material.warnings))
        self.warning_button.setVisible(bool(material.warnings))
        self.source_list.blockSignals(True)
        self.source_list.clear()
        for source in self._sources:
            self.source_list.addItem(self._new_source_item(f"{source.id}  ·  {source.locator}", source.id))
        self.source_list.setMaximumHeight(min(150, max(48, len(self._sources) * 44 + 8)))
        self.source_list.setCurrentRow(0 if self._sources else -1)
        self.source_list.blockSignals(False)
        self._on_source_changed(self.source_list.currentItem(), None)
        self._render_report(self._report)
        if mode == "local":
            self.report_tabs.setCurrentIndex(1)
        self.body_stack.setCurrentWidget(self.workspace_splitter)
        if self._online_warning:
            self.status_label.setText(self._online_warning)
        self._update_controls()
        return True

    def _on_mode_changed(self, index: int) -> None:
        if not self._material_id:
            return
        mode = self.mode_combo.currentData()
        self._material_modes[self._material_id] = mode
        self._report = self._local_report if mode == "local" else self._online_report
        self._render_report(self._report)
        if mode == "local":
            self.report_tabs.setCurrentIndex(1)
        self._set_reading_status()
        self._update_controls()

    def _show_extraction_warnings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("全部提取警告")
        dialog.resize(560, 400)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self.warning_label.text())
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _show_report_details(self) -> None:
        if not self._report:
            return
        meta = self._report.get("metadata", {})
        lines = [
            f"模型：{meta.get('model', '未知')}",
            f"生成时间：{meta.get('generated_at', '未知')}",
            f"已处理来源：{meta.get('analyzed_source_count', '?')}/{meta.get('total_source_count', '?')}",
            f"已引用来源：{meta.get('referenced_source_count', '旧报告未记录')}",
            "处理和引用数量均不代表语义完整性。",
            "未引用来源：",
            *meta.get("unreferenced_source_ids", []),
        ]
        if meta.get("mode") == "local":
            lines = ["本地规则（未调用模型）", f"识别标题：{meta.get('heading_count', 0)}", f"原文分段：{self._local_segment_count(self._report)}", "仅提供结构与来源索引，不进行内容理解。", "未引用来源：", *meta.get("unreferenced_source_ids", [])]
        dialog = QDialog(self)
        dialog.setWindowTitle("整理详情与未引用来源")
        dialog.resize(560, 400)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n".join(lines))
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _on_outline_selected(self, current, previous) -> None:
        if current is not None:
            refs = current.data(0, Qt.ItemDataRole.UserRole) or []
            for source_id in refs:
                if source_id in self._source_by_id:
                    self._on_report_source_clicked(QUrl(f"source:{source_id}"))
                    break

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
            source = self._source_by_id.get(str(source_id))
            locator = f" · {html.escape(source.locator)}" if source else ""
            links.append(f'<a href="source:{safe_id}">[{safe_id}{locator}]</a>')
        return " ".join(links)

    def _on_report_source_clicked(self, url: QUrl) -> None:
        source_id = url.toString().removeprefix("source:")
        for row in range(self.source_list.count()):
            if self.source_list.item(row).data(Qt.ItemDataRole.UserRole) == source_id:
                self.source_list.setCurrentRow(row)
                return

    @staticmethod
    def _local_segment_count(report: dict[str, Any]) -> int:
        def count(nodes):
            return sum((node.get("kind") == "source_segment") + count(node.get("children", [])) for node in nodes)
        return count(report.get("outline", []))

    @staticmethod
    def _outline_origin(node: dict[str, Any], local: bool) -> str:
        if local and node.get("kind") == "source_segment":
            return "原文分段"
        return "原文标题" if node.get("origin") == "original" else "自动归纳"

    def _render_report(self, report: dict[str, Any] | None) -> None:
        local_view = bool(report and report.get("metadata", {}).get("mode") == "local")
        self.report_tabs.setTabText(0, "说明" if local_view else "摘要")
        self.report_tabs.setTabText(1, "目录" if local_view else "大纲")
        self.outline_tree.clear()
        self.report_details_button.setVisible(bool(report))
        if not report:
            self.report_meta_label.clear()
            prompts = {
                "summary": "<h2>为这份资料生成整理报告</h2><p>填写你选择的 OpenAI 兼容服务配置，再点右上角“智能整理”。只有点击后才会发送文字。</p><p>分析会处理本文件中全部可提取文字，长资料可能分批多次请求并产生费用。</p>",
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
        local = meta.get("mode") == "local"
        analyzed = str(meta.get("analyzed_source_count", "?"))
        total = str(meta.get("total_source_count", "?"))
        model_name = str(meta.get("model", "未知"))
        if len(model_name) > 60:
            model_name = model_name[:57] + "…"
        info = f"已处理来源 {analyzed}/{total} 段（非语义覆盖率） · {model_name}"
        if "referenced_source_count" in meta:
            info += f" · 已引用来源 {meta['referenced_source_count']}"
        missing = meta.get("unreferenced_source_ids", [])
        if missing:
            preview = ", ".join(missing) if len(missing) <= 5 else f"{len(missing)} 段（查看整理详情）"
            info += " · 未引用来源：" + preview
        if local:
            info = f"本地规则（未调用模型） · 识别标题 {meta.get('heading_count', 0)} · 原文分段 {self._local_segment_count(report)}"
        self.report_meta_label.setTextFormat(Qt.TextFormat.PlainText)
        self.report_meta_label.setText(info)
        warnings = report.get("warnings", [])
        warning_html = "".join(f"<li>{html.escape(str(warning))}</li>" for warning in warnings)
        warning_section = f"<aside><h3>需要留意</h3><ul>{warning_html}</ul></aside>" if warnings else ""
        notice = "本地规则仅提供结构与来源索引，不进行内容理解。" if local else "AI 生成内容，请自行核对来源和结论。"
        self.report_views["summary"].setHtml(f"<h2>{title}</h2>{summary}{warning_section}<p><b>{notice}</b></p>")

        tree_items = 0

        def populate_tree(nodes, parent=None):
            nonlocal tree_items
            for node in nodes:
                tree_items += 1
                label = self._outline_origin(node, local)
                item = QTreeWidgetItem([f"{node.get('title', '')}【{label}】"])
                item.setData(0, Qt.ItemDataRole.UserRole, node.get("source_ids", []))
                item.setToolTip(0, str(node.get("summary", "")))
                if parent is None:
                    self.outline_tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                populate_tree(node.get("children", []), item)
        populate_tree(report.get("outline", []))
        self.outline_tree.setMaximumHeight(min(120, max(48, tree_items * 22 + 8)))
        self.outline_tree.expandAll()

        def outline_html(nodes: list[dict[str, Any]], depth: int = 0) -> str:
            chunks = []
            for node in nodes:
                chunks.append("<li><h3>" + html.escape(str(node.get("title", ""))) + ("【" + self._outline_origin(node, local) + "】") + "</h3><p>" + html.escape(str(node.get("summary", ""))) + "</p><p class='sourceRefs'>来源：" + self._source_link(node.get("source_ids", [])) + "</p>" + outline_html(node.get("children", []), depth + 1) + "</li>")
            return "<ol>" + "".join(chunks) + "</ol>" if chunks else ""
        outline = outline_html(report.get("outline", []))
        self.report_views["outline"].setHtml(outline or "<p>暂无提纲。</p>")
        chapters: dict[str, list[dict[str, Any]]] = {}
        for point in report.get("knowledge_points", []):
            chapters.setdefault(point.get("chapter") or "未标注章节", []).append(point)
        sections = []
        for chapter, points in chapters.items():
            sections.append(f"<h2>{html.escape(chapter)}</h2>")
            for point in points:
                sections.append(f"<h3>{html.escape(str(point.get('name', '')))}</h3>")
                for key, label in (("explanation", "说明"), ("definition", "定义"), ("key_points", "要点"), ("formulas", "公式"), ("conditions", "适用条件")):
                    value = point.get(key)
                    if value:
                        values = value if isinstance(value, list) else [value]
                        sections.append(f"<p><b>{label}</b>：" + "<br>".join(html.escape(str(v)) for v in values) + "</p>")
                sections.append("<p>来源：" + self._source_link(point.get("source_ids", [])) + "</p>")
        self.report_views["knowledge"].setHtml("<p><b>AI 归纳，非原文；请核对来源。</b></p>" + "".join(sections))
        suggestions = "".join(f"<li><p>{html.escape(str(item))}</p></li>" for item in report.get("learning_suggestions", []))
        self.report_views["suggestions"].setHtml(f"<ol>{suggestions}</ol><p><b>AI 生成内容，请自行核对来源和结论。</b></p>")
        if local:
            self.report_views["knowledge"].setHtml("<h2>知识点需要智能整理</h2><p>本地目录仅识别标题和原文分段。请配置模型并点击“智能整理”以获取知识点。</p>")
            self.report_views["suggestions"].setHtml("<h2>学习建议需要智能整理</h2><p>本地规则不提供内容理解或学习建议。点击“智能整理”才会调用模型。</p>")

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
        try:
            started = self.jobs.start_analysis(self._material_id, config)
        except Exception:
            self.status_label.setText("智能整理启动失败；已有报告与本地目录仍保留。请检查模型配置。")
            self._update_controls()
            return
        if started:
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
        self.mode_combo.setEnabled(has_material and not busy and accepting)
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
                self.warning_button.show()
        elif kind == "analysis":
            material_id = payload.get("material_id")
            report = payload.get("report")
            if self._cancel_requested or not isinstance(report, dict) or material_id != self._material_id:
                return
            try:
                self.service.save_analysis(material_id, report)
            except (ValueError, OSError, sqlite3.Error):
                self.status_label.setText("分析完成，但报告保存失败；请重试。")
                return
            self._online_report = report
            self._online_warning = ""
            self._material_modes[material_id] = "online"
            self.mode_combo.setCurrentIndex(self.mode_combo.findData("online"))
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
        except (ValueError, OSError, sqlite3.Error):
            QMessageBox.warning(self, "导出失败", "无法读取资料信息，报告未导出。")
            return
        suffix = ".md" if extension == "md" else ".json"
        report_suffix = "outline" if self.mode_combo.currentData() == "local" else "analysis"
        path, _ = QFileDialog.getSaveFileName(self, "导出整理报告", f"{Path(material.filename).stem}-{report_suffix}{suffix}", "Markdown (*.md)" if extension == "md" else "JSON (*.json)")
        if path:
            self.export_report(path, extension)

    def _export_payload(self) -> dict[str, Any]:
        assert self._material_id and self._report
        material = self.service.get_material(self._material_id)
        sources = self.service.get_sources(self._material_id)
        return {
            "material": {"filename": material.filename, "kind": material.kind, "imported_at": material.imported_at, "character_count": material.character_count, "source_count": material.source_count, "warnings": list(material.warnings)},
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
        if destination.exists():
            answer = QMessageBox.question(self, "确认覆盖", f"最终目标已存在：{destination}\n是否覆盖？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self.status_label.setText("已取消导出，原有文件未更改。")
                return
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
        local = metadata.get("mode") == "local"
        notice = "本地规则（未调用模型）；仅提供结构与来源索引，不进行内容理解。" if local else "AI 生成内容，请结合来源原文核对。"
        lines = [f"# {report.get('title') or payload['material']['filename']}", "", f"资料文件：{payload['material']['filename']}", "", notice, "", "## 结构信息" if local else "## 摘要", "", str(report.get("summary", "")), "", "## 结构提纲"]

        def write_outline(nodes: list[dict[str, Any]], depth: int = 0) -> None:
            for node in nodes:
                refs = ", ".join(node.get("source_ids", []))
                origin = MainWindow._outline_origin(node, local)
                lines.append(f"{'  ' * depth}- **{node.get('title', '')}**【{origin}】：{node.get('summary', '')}（来源：{refs}）")
                write_outline(node.get("children", []), depth + 1)
        write_outline(report.get("outline", []))
        lines += ["", "## 知识点"]
        lines += ["", "知识点需要智能整理；本地规则不提供知识点。" if local else "AI 归纳，非原文。"]
        chapters: dict[str, list[dict[str, Any]]] = {}
        for point in report.get("knowledge_points", []):
            chapters.setdefault(point.get("chapter") or "未标注章节", []).append(point)
        for chapter, points in chapters.items():
            lines += ["", f"### {chapter}"]
            for point in points:
                lines.append(f"- **{point.get('name', '')}**：{point.get('explanation', '')}（来源：{', '.join(point.get('source_ids', []))}）")
                for key, label in (("definition", "定义"), ("key_points", "要点"), ("formulas", "公式"), ("conditions", "适用条件")):
                    value = point.get(key)
                    if value:
                        for entry in value if isinstance(value, list) else [value]:
                            lines.append(f"  - {label}：{entry}")
        lines += ["", "## 学习建议"]
        lines.extend(f"- {item}" for item in report.get("learning_suggestions", []))
        lines += ["", "## 分析信息", f"- 模型：{metadata.get('model', '未知')}", f"- 生成时间：{metadata.get('generated_at', '未知')}", f"- 已处理来源：{metadata.get('analyzed_source_count', '?')}/{metadata.get('total_source_count', '?')} 段（非语义覆盖率）"]
        if "referenced_source_count" in metadata:
            lines.append(f"- 已引用来源：{metadata['referenced_source_count']}")
        if "unreferenced_source_ids" in metadata:
            lines.append("- 未引用来源：" + ", ".join(metadata["unreferenced_source_ids"]))
        if local:
            start = lines.index("## 分析信息")
            lines[start:] = ["## 本地整理信息", "- 规则：本地规则（未调用模型）", f"- 资料导入时间：{metadata.get('generated_at', '未知')}", f"- 识别标题：{metadata.get('heading_count', 0)}", f"- 原文分段：{MainWindow._local_segment_count(report)}", "", "## 规则限制", "仅识别标题和原文分段，不提炼内容含义；未识别的标题可能保留在原文分段中。"]
        lines += ["", "## 提取限制", "扫描件需 OCR；复杂公式、表格及版式可能丢失，请对照原文件。"]
        lines.extend(f"- {warning}" for warning in payload["material"].get("warnings", []))
        lines += ["", "## 规则提示" if local else "## 分析提示"]
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
