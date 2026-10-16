"""Local storage and text extraction for user supplied study materials."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import uuid
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

from .material_types import MaterialInfo, MaterialImportResult, MaterialSource

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_ZIP_MEMBERS = 10_000
MAX_CHARACTERS = 600_000
MAX_SOURCES = 300
SOURCE_CHARS = 3000
_DOCX_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class _MaterialInputError(ValueError):
    """A safe, user-facing validation error for an uploaded document."""


def _safe_filename(filename: str) -> str:
    name = Path(str(filename).replace("\\", "/")).name
    name = re.sub(r"[^\w.() \-]+", "_", name, flags=re.UNICODE).strip(" .")
    if not name:
        return "upload"
    suffix = Path(name).suffix
    if len(name) > 180:
        base = name[:-len(suffix)] if suffix else name
        name = base[:max(1, 180 - len(suffix))] + suffix
    return name


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n").replace("\x00", "")
    cleaned = "".join(ch if ch in "\n\t" or ord(ch) >= 32 else "�" for ch in text).strip("\n")
    return cleaned if cleaned.strip() else ""


def _decode_text(raw: bytes) -> str:
    encodings = ("utf-16",) if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if any(ord(ch) < 32 and ch not in "\r\n\t\f" for ch in text):
            raise _MaterialInputError("文本文件包含二进制或损坏内容，请另存为 UTF-8 后重试。")
        return _clean(text)
    raise _MaterialInputError("文本文件编码无法读取或内容损坏，请另存为 UTF-8 后重试。")


def _split_entries(entries: list[tuple[str, int | None, int | None]], kind: str) -> list[MaterialSource]:
    """Pack paragraphs into bounded sources while retaining their start locator."""
    result: list[MaterialSource] = []
    buf = ""
    start_page: int | None = None
    start_para: int | None = None
    stored_chars = 0

    def flush():
        nonlocal buf
        if buf:
            result.append(MaterialSource(f"S{len(result)+1}", buf, _locator(kind, start_page, start_para), start_page, start_para))
            if len(result) > MAX_SOURCES:
                raise _MaterialInputError("单个文件最多生成 300 个来源片段，请拆分文件后再导入。")
            buf = ""

    for text, page, paragraph in entries:
        text = _clean(text)
        if not text:
            continue
        # Slice lazily so a very long paragraph does not create a large pieces list.
        for offset in range(0, len(text), SOURCE_CHARS):
            piece = text[offset:offset + SOURCE_CHARS]
            if buf and (len(buf) + 2 + len(piece) > SOURCE_CHARS or (page is not None and start_page != page)):
                flush()
            if not buf:
                start_page, start_para = page, paragraph
            stored_chars += len(piece) + (2 if buf else 0)
            if stored_chars > MAX_CHARACTERS:
                raise _MaterialInputError("文件文字超过 600,000 个字符，请拆分文件后再导入。")
            buf = f"{buf}\n\n{piece}" if buf else piece
    flush()
    return result


def _locator(kind: str, page: int | None, paragraph: int | None) -> str:
    if page is not None:
        return f"第 {page} 页"
    if paragraph is not None:
        return f"第 {paragraph} 段起"
    return "文档"


def _extract_pdf(raw: bytes) -> tuple[list[MaterialSource], list[str]]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(raw), strict=True)
        if reader.is_encrypted:
            if reader.decrypt("") == 0:
                raise _MaterialInputError("暂不支持带密码的 PDF 文件。")
        entries: list[tuple[str, int | None, int | None]] = []
        warnings: list[str] = []
        empty_pages = 0
        total_pages = len(reader.pages)
        for page_no, page in enumerate(reader.pages, 1):
            try:
                text = _clean(page.extract_text() or "")
            except Exception:
                empty_pages += 1
                warnings.append(f"第 {page_no} 页文字提取失败，内容未导入；请检查原 PDF。")
                continue
            if not text:
                empty_pages += 1
                warnings.append(f"第 {page_no} 页没有可读取的文字，可能是扫描页或空白页；当前不支持 OCR。")
                continue
            for para_no, paragraph in enumerate((p.strip() for p in re.split(r"\n\s*\n", text)), 1):
                if paragraph:
                    entries.append((paragraph, page_no, para_no))
        if total_pages and empty_pages == total_pages:
            raise _MaterialInputError("PDF 中没有可读取的文字；当前不支持对扫描版 PDF 进行 OCR。")
        if entries:
            # PDF text has no reliable semantic heading/table structure. Avoid
            # guessing from font size or pretending that formulas are complete.
            warnings.append("PDF 按可读取文字提取；标题层级、阅读顺序及公式、图表可能丢失或错乱，请对照原文核对（不支持 OCR）。")
        return _split_entries(entries, "pdf"), warnings
    except _MaterialInputError:
        raise
    except Exception as exc:
        raise ValueError("PDF 文件损坏或无法读取，请检查文件后重试。") from exc


def _extract_docx(raw: bytes) -> tuple[list[MaterialSource], list[str]]:
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_MEMBERS:
                raise ValueError("DOCX 压缩包中的文件数量超过限制。")
            if sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("DOCX 解压后超过 64 MB 限制。")
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                path = Path(normalized)
                if path.is_absolute() or ".." in normalized.split("/") or re.match(r"^[A-Za-z]:", normalized):
                    raise ValueError("DOCX 压缩包包含不安全的路径。")
            if "word/document.xml" not in archive.namelist():
                raise ValueError("DOCX 文件无效：缺少正文内容。")

            def read_xml(name):
                if archive.namelist().count(name) != 1:
                    raise ValueError("DOCX 文件包含重复的 XML 内容。")
                data = archive.read(name)
                # Also cover UTF-16/32 XML declarations without expanding entities.
                if b"<!DOCTYPE" in data.replace(b"\x00", b"").upper():
                    raise ValueError("DOCX XML 包含不安全的 DTD 声明。")
                return ET.fromstring(data)

            root = read_xml("word/document.xml")
            if root.tag != f"{{{_DOCX_NS}}}document" or root.find(f"{{{_DOCX_NS}}}body") is None:
                raise ValueError("DOCX 文件无效：缺少正文内容。")
            styles_root = read_xml("word/styles.xml") if "word/styles.xml" in archive.namelist() else None
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("DOCX 文件损坏或无法读取，请检查文件后重试。") from exc

    ns = {"w": _DOCX_NS}
    w = "{" + _DOCX_NS + "}"
    styles = {} if styles_root is None else {
        node.get(w + "styleId"): node for node in styles_root.findall("w:style", ns)
    }

    def value(node, path):
        found = node.find(path, ns)
        return found.get(w + "val") if found is not None else None

    def heading_level(paragraph):
        outline = value(paragraph, "w:pPr/w:outlineLvl")
        style_id = value(paragraph, "w:pPr/w:pStyle")
        seen = set()
        while outline is None and style_id and style_id not in seen:
            seen.add(style_id)
            style = styles.get(style_id)
            if style is not None:
                outline = value(style, "w:pPr/w:outlineLvl")
            if outline is not None:
                break
            name = value(style, "w:name") if style is not None else None
            match = (re.fullmatch(r"(?:heading|标题)\s*([1-9])", style_id, re.I)
                     or re.fullmatch(r"(?:heading|标题)\s*([1-9])", name or "", re.I))
            if match:
                return int(match.group(1))
            style_id = value(style, "w:basedOn") if style is not None else None
        return int(outline) + 1 if outline in {"0", "1", "2", "3", "4", "5", "6", "7", "8"} else None

    def children(element):
        # OOXML supplies alternate representations of the same content; never
        # extract both Choice and Fallback (notably duplicated text boxes).
        mc = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
        if element.tag == mc + "AlternateContent":
            branch = element.find(mc + "Choice")
            if branch is None:
                branch = element.find(mc + "Fallback")
            return () if branch is None else (branch,)
        return element

    entries: list[tuple[str, int | None, int | None]] = []
    has_deletions = False
    has_graphics = False
    has_math = False
    para_no = 0

    def walk(element):
        nonlocal has_deletions, has_graphics, has_math, para_no
        if element.tag in (w + "del", w + "delText", w + "moveFrom"):
            has_deletions = True
            return
        if element.tag in (w + "drawing", w + "pict", w + "object"):
            has_graphics = True
        if element.tag.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/math}"):
            has_math = True
        if element.tag == w + "p":
            para_no += 1
            number = para_no
            level = heading_level(element)
            own_chunks = []

            def emit():
                nonlocal level
                text = _clean("".join(own_chunks))
                if text:
                    entries.append((("#" * level + " " if level else "") + text, None, number))
                    level = None
                own_chunks.clear()

            # Nested text-box paragraphs must not also become outer run text.
            def inline(node):
                if node.tag == w + "p":
                    emit()
                    walk(node)
                elif node.tag in (w + "del", w + "delText", w + "moveFrom"):
                    walk(node)
                else:
                    walk_inline(node)

            def walk_inline(node):
                nonlocal has_graphics, has_math
                if node.tag in (w + "drawing", w + "pict", w + "object"):
                    has_graphics = True
                if node.tag.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/math}"):
                    has_math = True
                if node.tag == w + "t" and node.text:
                    own_chunks.append(node.text)
                elif node.tag == w + "tab":
                    own_chunks.append("\t")
                elif node.tag in (w + "br", w + "cr"):
                    own_chunks.append("\n")
                for child in children(node):
                    inline(child)

            for child in children(element):
                inline(child)
            emit()
            return
        for child in children(element):
            walk(child)

    try:
        walk(root.find("w:body", ns))
    except RecursionError as exc:
        raise ValueError("DOCX XML 层级过深，无法安全读取。") from exc
    sources = _split_entries(entries, "docx")
    if not sources:
        raise ValueError("DOCX 中没有可读取的文字。")
    warnings = ["已忽略 DOCX 中标记为删除的修订内容。"] if has_deletions else []
    if has_graphics:
        warnings.append("DOCX 包含图片、图表或嵌入对象；仅提取可读取的正文文字，图像内容可能缺失，请核对原文（不支持 OCR）。")
    if has_math:
        warnings.append("DOCX 包含公式；当前不还原公式结构，相关内容可能缺失，请对照原文核对。")
    return sources, warnings


def _extract(filename: str, raw: bytes) -> tuple[list[MaterialSource], list[str], str]:
    extension = Path(filename.replace("\\", "/")).suffix.casefold()
    if extension == ".pdf":
        sources, warnings = _extract_pdf(raw)
        kind = "pdf"
    elif extension == ".docx":
        sources, warnings = _extract_docx(raw)
        kind = "docx"
    elif extension in (".txt", ".md"):
        text = _decode_text(raw)
        sources = _split_entries([(p, None, i) for i, p in enumerate(re.split(r"\n\s*\n", text), 1)], extension[1:])
        warnings = []
        kind = extension[1:]
    else:
        raise _MaterialInputError("文件类型不受支持，请上传可选择文字的 PDF、DOCX、TXT 或 MD 文件。")
    if not sources:
        raise _MaterialInputError("文件为空或没有可读取的文字。")
    return sources, warnings, kind


class MaterialService:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir or os.getenv("CSA_DATA_DIR") or Path(__file__).resolve().parents[1] / "data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "material.sqlite3"
        self._init_db()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init_db(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS materials(
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, kind TEXT NOT NULL,
                    imported_at TEXT NOT NULL, sha256 TEXT UNIQUE NOT NULL,
                    raw BLOB NOT NULL, character_count INTEGER NOT NULL, warnings TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS material_sources(
                    material_id TEXT NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL, text TEXT NOT NULL, locator TEXT NOT NULL,
                    page INTEGER, paragraph INTEGER, PRIMARY KEY(material_id, source_id)
                );
                CREATE TABLE IF NOT EXISTS material_analyses(
                    material_id TEXT PRIMARY KEY REFERENCES materials(id) ON DELETE CASCADE,
                    report TEXT NOT NULL, updated_at TEXT NOT NULL
                );
            """)

    @staticmethod
    def _info(row: sqlite3.Row, source_count: int) -> MaterialInfo:
        import json
        return MaterialInfo(row["id"], row["filename"], row["kind"], row["imported_at"], source_count,
                            row["character_count"], json.loads(row["warnings"]))

    def import_document(self, filename: str, raw: bytes) -> MaterialImportResult:
        if not isinstance(raw, bytes):
            raw = bytes(raw)
        if not raw:
            raise _MaterialInputError("上传的文件为空。")
        if len(raw) > MAX_FILE_BYTES:
            raise _MaterialInputError("文件大小不能超过 20 MB。")
        safe_name = _safe_filename(filename)
        sources, warnings, kind = _extract(safe_name, raw)
        digest = hashlib.sha256(raw).hexdigest()
        import json
        with self._connect() as db:
            existing = db.execute("SELECT * FROM materials WHERE sha256=?", (digest,)).fetchone()
            if existing:
                count = db.execute("SELECT count(*) FROM material_sources WHERE material_id=?", (existing["id"],)).fetchone()[0]
                info = self._info(existing, count)
                return MaterialImportResult(info, True, list(info.warnings))
            material_id = uuid.uuid4().hex
            imported_at = datetime.now(timezone.utc).isoformat()
            char_count = sum(len(source.text) for source in sources)
            db.execute("INSERT INTO materials VALUES(?,?,?,?,?,?,?,?)", (
                material_id, safe_name, kind, imported_at, digest, sqlite3.Binary(raw), char_count, json.dumps(warnings, ensure_ascii=False)))
            db.executemany("INSERT INTO material_sources VALUES(?,?,?,?,?,?)", [
                (material_id, s.id, s.text, s.locator, s.page, s.paragraph) for s in sources])
            return MaterialImportResult(MaterialInfo(material_id, safe_name, kind, imported_at, len(sources), char_count, list(warnings)), False, warnings)

    def list_materials(self) -> list[MaterialInfo]:
        with self._connect() as db:
            rows = db.execute("SELECT m.*, count(s.source_id) source_count FROM materials m LEFT JOIN material_sources s ON s.material_id=m.id GROUP BY m.id ORDER BY m.imported_at DESC, m.id").fetchall()
        return [self._info(row, row["source_count"]) for row in rows]

    def get_material(self, id: str) -> MaterialInfo:
        with self._connect() as db:
            row = db.execute("SELECT * FROM materials WHERE id=?", (id,)).fetchone()
            if row is None:
                raise ValueError("找不到该资料。")
            count = db.execute("SELECT count(*) FROM material_sources WHERE material_id=?", (id,)).fetchone()[0]
        return self._info(row, count)

    def get_sources(self, id: str) -> list[MaterialSource]:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM materials WHERE id=?", (id,)).fetchone() is None:
                raise ValueError("找不到该资料。")
            rows = db.execute("SELECT * FROM material_sources WHERE material_id=? ORDER BY CAST(substr(source_id,2) AS INTEGER)", (id,)).fetchall()
        return [MaterialSource(row["source_id"], row["text"], row["locator"], row["page"], row["paragraph"]) for row in rows]

    def get_raw(self, id: str) -> bytes:
        with self._connect() as db:
            row = db.execute("SELECT raw FROM materials WHERE id=?", (id,)).fetchone()
            if row is None:
                raise ValueError("找不到该资料。")
            return bytes(row["raw"])

    def delete_material(self, id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM materials WHERE id=?", (id,))

    def save_analysis(self, id: str, report: dict) -> None:
        import json
        if not isinstance(report, dict):
            raise ValueError("分析报告格式无效。")
        serialized = json.dumps(report, ensure_ascii=False, allow_nan=False)
        with self._connect() as db:
            if db.execute("SELECT 1 FROM materials WHERE id=?", (id,)).fetchone() is None:
                raise ValueError("找不到该资料。")
            db.execute("INSERT INTO material_analyses VALUES(?,?,?) ON CONFLICT(material_id) DO UPDATE SET report=excluded.report, updated_at=excluded.updated_at",
                       (id, serialized, datetime.now(timezone.utc).isoformat()))

    def get_analysis(self, id: str) -> dict | None:
        import json
        with self._connect() as db:
            if db.execute("SELECT 1 FROM materials WHERE id=?", (id,)).fetchone() is None:
                raise ValueError("找不到该资料。")
            row = db.execute("SELECT report FROM material_analyses WHERE material_id=?", (id,)).fetchone()
        return json.loads(row["report"]) if row else None
