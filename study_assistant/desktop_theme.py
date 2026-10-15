"""Warm, quiet visual system and small vector artwork for the Qt desktop UI."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

BG = "#F3F0E9"
PAPER = "#FFFEFB"
INK = "#202B3A"
MUTED = "#657080"
NAVY = "#202B3A"
ACCENT = "#CC754F"
LIGHTACCENT = "#F6E7DA"
LINE = "#E4DFD5"


APP_STYLE = f"""
QMainWindow, QDialog {{ background: {BG}; color: {INK}; }}
QWidget {{ color: {INK}; font-size: 10pt; selection-background-color: {LIGHTACCENT}; selection-color: {INK}; }}
QWidget#desktopRoot, QWidget#contentArea, QStackedWidget#bodyStack, QWidget#emptyLibraryPage {{ background: {BG}; }}
QWidget#sidebar, QWidget#sidePanel {{ background: {NAVY}; color: {PAPER}; }}
QWidget#sidebar QLabel, QWidget#sidePanel QLabel {{ color: {PAPER}; }}
QFrame#libraryCard {{ background: #2B384A; border: 1px solid #3C4A5D; border-radius: 12px; }}
QFrame#libraryCard[active="true"] {{ background: #384B61; border: 1px solid {ACCENT}; }}
QFrame#libraryCard QLabel#libraryFilename {{ color: {PAPER}; font-size: 12px; font-weight: 600; }}
QFrame#libraryCard QLabel#libraryFileStats, QLabel#libraryFileStats {{ color: #ADB9C9; font-size: 11px; }}
QFrame#contentPanel {{ background: {PAPER}; border: 0; border-radius: 14px; }}
QFrame#mainToolbar {{ background: {BG}; border: 0; border-bottom: 1px solid {LINE}; }}
QToolBar::separator {{ width: 1px; background: {LINE}; margin: 5px 7px; }}
QLabel#brandTitle {{ color: {PAPER}; font-size: 19px; font-weight: 700; }}
QLabel#brandSubtitle {{ color: #C6CDD5; font-size: 11px; }}
QLabel#libraryTitle {{ color: {PAPER}; font-size: 15px; font-weight: 650; }}
QLabel#libraryCount {{ color: #C6CDD5; font-size: 11px; }}
QLabel#fileTitleLabel {{ color: {INK}; font-size: 22px; font-weight: 650; }}
QLabel#fileStatsLabel, QLabel#reportMetaLabel {{ color: {MUTED}; font-size: 11px; }}
QLabel#sectionTitle {{ color: {INK}; font-size: 16px; font-weight: 650; }}
QLabel#emptyLibraryTitle {{ color: {INK}; font-size: 22px; font-weight: 650; }}
QLabel#emptyLibraryCopy {{ color: {MUTED}; font-size: 13px; }}
QLabel#brandIcon {{ background: transparent; }}
QPushButton {{ min-height: 34px; padding: 5px 13px; border: 1px solid {LINE}; border-radius: 9px; background: {PAPER}; color: {INK}; }}
QPushButton:hover {{ border-color: {ACCENT}; color: {INK}; background: #FFF9F4; }}
QPushButton:pressed {{ background: {LIGHTACCENT}; }}
QPushButton:focus, QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTextBrowser:focus, QTabWidget:focus {{ border: 1px solid {ACCENT}; }}
QPushButton:disabled {{ color: #A5A8A9; background: #ECE9E2; border-color: {LINE}; }}
QPushButton#primaryButton, QPushButton#analyzeButton, QPushButton[class="primaryButton"] {{ background: #B65C36; border-color: #B65C36; color: {PAPER}; font-weight: 600; }}
QPushButton#primaryButton:hover, QPushButton#analyzeButton:hover, QPushButton[class="primaryButton"]:hover {{ background: #AA4E2A; border-color: #AA4E2A; }}
QPushButton#primaryButton:disabled, QPushButton#analyzeButton:disabled, QPushButton[class="primaryButton"]:disabled {{ background: #D7B5A5; border-color: #D7B5A5; color: #FFFDF9; }}
QPushButton#toolButton, QPushButton[class="toolButton"] {{ background: transparent; border-color: transparent; color: {INK}; }}
QPushButton#toolButton:hover, QPushButton[class="toolButton"]:hover {{ background: {LIGHTACCENT}; border-color: {LIGHTACCENT}; }}
QWidget#sidebar QPushButton[class="toolButton"], QWidget#sidebar QPushButton#deleteMaterialButton {{ background: transparent; border-color: transparent; color: #E6E9EE; }}
QWidget#sidebar QPushButton[class="toolButton"]:hover, QWidget#sidebar QPushButton#deleteMaterialButton:hover {{ background: #344052; border-color: #344052; color: {PAPER}; }}
QWidget#sidebar QPushButton[class="toolButton"]:disabled, QWidget#sidebar QPushButton#deleteMaterialButton:disabled {{ background: transparent; border-color: transparent; color: #AAB5C3; }}
QToolBar QToolButton {{ color: {INK}; padding: 6px 9px; border-radius: 8px; }}
QToolBar QToolButton:hover {{ color: {INK}; background: {LIGHTACCENT}; }}
QToolBar QToolButton:disabled {{ color: #A5A8A9; }}
QPushButton#importButton {{ background: #344052; border-color: #566174; color: {PAPER}; }}
QPushButton#importButton:hover {{ background: #3D4B60; border-color: {ACCENT}; }}
QPushButton#deleteMaterialButton, QPushButton#cancelButton {{ color: #8D5140; }}
QPushButton#deleteMaterialButton:hover, QPushButton#cancelButton:hover {{ background: #F9EAE5; border-color: #D99A82; }}
QListWidget#materialList {{ background: transparent; border: 0; padding: 5px 3px; outline: 0; color: {PAPER}; }}
QListWidget#materialList::item {{ padding: 0; margin: 3px 0; border: 0; background: transparent; }}
QListWidget#materialList::item:hover, QListWidget#materialList::item:selected {{ background: transparent; border: 0; }}
QListWidget#sourceList {{ background: transparent; border: 0; border-bottom: 1px solid {LINE}; border-radius: 0; padding: 2px 0 7px 0; outline: 0; }}
QListWidget#sourceList::item {{ padding: 9px; border-radius: 7px; }}
QListWidget#sourceList::item:selected {{ background: {LIGHTACCENT}; color: {INK}; }}
QPlainTextEdit#sourcePreview {{ background: transparent; border: 0; border-radius: 0; padding: 9px 2px; selection-background-color: {LIGHTACCENT}; }}
QTextBrowser {{ background: {PAPER}; border: 1px solid {LINE}; border-radius: 12px; padding: 10px; selection-background-color: {LIGHTACCENT}; }}
QTabWidget#reportTabs::pane {{ background: {PAPER}; border: 0; border-radius: 12px; top: -1px; }}
QTabWidget#reportTabs QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 9px 14px; margin-right: 4px; border: 0; border-radius: 8px; }}
QTabWidget#reportTabs QTabBar::tab:hover {{ color: {INK}; background: #ECE8E0; }}
QTabWidget#reportTabs QTabBar::tab:selected {{ background: {LIGHTACCENT}; color: {INK}; font-weight: 600; }}
QLineEdit {{ min-height: 32px; padding: 5px 9px; background: {PAPER}; border: 1px solid {LINE}; border-radius: 8px; }}
QLabel#mutedLabel, QLabel#statusLabel, QLabel#materialStats {{ color: {MUTED}; }}
QLabel#settingsPrivacyNotice {{ background: {BG}; color: {MUTED}; border-radius: 9px; padding: 10px; }}
QLabel[class="warningBanner"] {{ background: {LIGHTACCENT}; color: #754431; border: 1px solid #E8C5B1; border-radius: 9px; padding: 8px 10px; }}
QProgressBar#jobProgress {{ min-height: 9px; max-height: 9px; border: 0; border-radius: 4px; background: #E8E3DA; text-align: center; color: transparent; }}
QProgressBar#jobProgress::chunk {{ border-radius: 4px; background: {ACCENT}; }}
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 24px; background: #CFC9BF; border-radius: 4px; }}
QScrollBar::handle:vertical:hover {{ background: #ADA69B; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; background: transparent; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; margin: 2px; }}
QScrollBar::handle:horizontal {{ min-width: 24px; background: #CFC9BF; border-radius: 4px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal, QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ width: 0; background: transparent; }}
QToolTip {{ background: {INK}; color: {PAPER}; border: 0; padding: 6px 8px; border-radius: 5px; }}
"""


def apply_theme(widget: QWidget) -> None:
    """Apply the shared stylesheet and a CJK-capable system font."""
    widget.setStyleSheet(APP_STYLE)
    # Keep the preferred families in the fallback list even on Qt builds whose
    # font database cannot enumerate every installed Windows font.
    font = QFont()
    font.setFamilies(["Microsoft YaHei UI", "Noto Sans SC", QApplication.font().family()])
    font.setPointSizeF(10.0)
    widget.setFont(font)


def make_icon(name: str, color: str = ACCENT, size: int = 20) -> QIcon:
    """Draw a crisp, dependency-free 24px vector icon into a QIcon."""
    if size < 8:
        raise ValueError("图标尺寸至少为8像素。")
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24.0, size / 24.0)
    ink = QColor(color)
    pen = QPen(ink, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()

    if name == "book":
        path.moveTo(12, 6); path.cubicTo(9, 4, 5, 4, 3.5, 5.5); path.lineTo(3.5, 19); path.cubicTo(7, 17.8, 10, 18.6, 12, 20)
        path.moveTo(12, 6); path.cubicTo(15, 4, 19, 4, 20.5, 5.5); path.lineTo(20.5, 19); path.cubicTo(17, 17.8, 14, 18.6, 12, 20)
        path.moveTo(12, 6); path.lineTo(12, 20)
    elif name == "upload":
        path.moveTo(12, 15); path.lineTo(12, 4); path.moveTo(7.5, 8.5); path.lineTo(12, 4); path.lineTo(16.5, 8.5)
        path.moveTo(5, 15); path.lineTo(5, 19); path.quadTo(5, 20, 6, 20); path.lineTo(18, 20); path.quadTo(19, 20, 19, 19); path.lineTo(19, 15)
    elif name == "settings":
        for y in (6, 12, 18):
            path.moveTo(4, y); path.lineTo(20, y)
        painter.setBrush(QColor(PAPER))
        painter.drawEllipse(QPointF(9, 6), 2.1, 2.1)
        painter.drawEllipse(QPointF(15, 12), 2.1, 2.1)
        painter.drawEllipse(QPointF(8, 18), 2.1, 2.1)
    elif name == "trash":
        path.moveTo(5, 7); path.lineTo(19, 7); path.moveTo(9, 7); path.lineTo(9, 4.5); path.lineTo(15, 4.5); path.lineTo(15, 7)
        path.moveTo(7, 7); path.lineTo(8, 20); path.lineTo(16, 20); path.lineTo(17, 7)
        path.moveTo(10, 10); path.lineTo(10, 17); path.moveTo(14, 10); path.lineTo(14, 17)
    elif name == "download":
        path.moveTo(12, 4); path.lineTo(12, 15); path.moveTo(7.5, 10.5); path.lineTo(12, 15); path.lineTo(16.5, 10.5)
        path.moveTo(5, 16); path.lineTo(5, 19); path.quadTo(5, 20, 6, 20); path.lineTo(18, 20); path.quadTo(19, 20, 19, 19); path.lineTo(19, 16)
    elif name == "sparkle":
        path.moveTo(12, 3); path.cubicTo(12.8, 8.7, 15, 11, 21, 12); path.cubicTo(15, 13, 13, 15.3, 12, 21); path.cubicTo(11, 15.3, 8.8, 13, 3, 12); path.cubicTo(8.8, 11, 11, 8.7, 12, 3)
        path.moveTo(19, 3); path.lineTo(19, 7); path.moveTo(17, 5); path.lineTo(21, 5)
    elif name == "stop":
        painter.setBrush(ink)
        painter.drawRoundedRect(QRectF(5.5, 5.5, 13, 13), 2, 2)
    elif name == "chevron":
        path.moveTo(9, 5); path.lineTo(16, 12); path.lineTo(9, 19)
    elif name in {"file", "pdf", "text"}:
        path.moveTo(6, 3.5); path.lineTo(14.5, 3.5); path.lineTo(19, 8); path.lineTo(19, 20); path.quadTo(19, 21, 18, 21); path.lineTo(6, 21); path.quadTo(5, 21, 5, 20); path.lineTo(5, 4.5); path.quadTo(5, 3.5, 6, 3.5)
        path.moveTo(14, 4); path.lineTo(14, 8.5); path.lineTo(18.5, 8.5)
        if name == "text":
            path.moveTo(8, 12); path.lineTo(16, 12); path.moveTo(8, 15); path.lineTo(16, 15); path.moveTo(8, 18); path.lineTo(14, 18)
        elif name == "pdf":
            painter.setFont(QFont("Arial", 4, QFont.Weight.Bold))
            painter.drawText(QRectF(7, 11.5, 10, 5), Qt.AlignmentFlag.AlignCenter, "PDF")
    else:
        # Keep callers resilient to an unrecognized icon name.
        path.moveTo(6, 3.5); path.lineTo(14.5, 3.5); path.lineTo(19, 8); path.lineTo(19, 20); path.quadTo(19, 21, 18, 21); path.lineTo(6, 21); path.quadTo(5, 21, 5, 20); path.lineTo(5, 4.5); path.quadTo(5, 3.5, 6, 3.5)
        path.moveTo(14, 4); path.lineTo(14, 8.5); path.lineTo(18.5, 8.5)

    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


class EmptyLibraryArt(QWidget):
    """A small transparent vector illustration for the empty-library state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyLibraryArt")
        self.setFixedSize(160, 112)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def sizeHint(self) -> QSize:
        return QSize(160, 112)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 200.0, self.height() / 140.0)
        # Quiet sun and paper shapes, leaving the widget background transparent.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#F0C9A8"))
        painter.drawEllipse(QRectF(140, 13, 32, 32))
        painter.setPen(QPen(QColor("#D9A27D"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for start, end in ((QPointF(156, 4), QPointF(156, 0)), (QPointF(178, 29), QPointF(184, 29)), (QPointF(135, 20), QPointF(130, 17))):
            painter.drawLine(start, end)

        painter.setPen(QPen(QColor(LINE), 1.2))
        painter.setBrush(QColor("#F8F4EC"))
        painter.drawRoundedRect(QRectF(33, 42, 126, 80), 8, 8)
        painter.setBrush(QColor(PAPER))
        painter.drawRoundedRect(QRectF(45, 34, 126, 80), 8, 8)

        painter.setPen(QPen(QColor(ACCENT), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        book = QPainterPath()
        book.moveTo(108, 61); book.cubicTo(94, 54, 77, 55, 67, 62); book.lineTo(67, 99); book.cubicTo(82, 93, 97, 95, 108, 102)
        book.moveTo(108, 61); book.cubicTo(122, 54, 139, 55, 149, 62); book.lineTo(149, 99); book.cubicTo(134, 93, 119, 95, 108, 102)
        book.moveTo(108, 61); book.lineTo(108, 102)
        painter.drawPath(book)

        painter.setPen(QPen(QColor("#D6CFC3"), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(48, 47), QPointF(74, 47))
        painter.drawLine(QPointF(48, 53), QPointF(67, 53))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT))
        painter.drawEllipse(QRectF(26, 31, 5, 5))
        painter.setBrush(QColor("#D8B884"))
        painter.drawEllipse(QRectF(171, 73, 4, 4))
        painter.end()
