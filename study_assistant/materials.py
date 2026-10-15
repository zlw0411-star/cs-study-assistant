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
    text = text.replace("\x00", "")
    return "".join(ch if ch in "\n\r\t" or ord(ch) >= 32 else "�" for ch in text).strip()


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return _clean(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return _clean(raw.decode("utf-8", errors="replace"))


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
            if buf and (len(buf) + 1 + len(piece) > SOURCE_CHARS or (page is not None and start_page != page)):
                flush()
            if not buf:
                start_page, start_para = page, paragraph
            stored_chars += len(piece) + (1 if buf else 0)
            if stored_chars > MAX_CHARACTERS:
                raise _MaterialInputError("文件文字超过 600,000 个字符，请拆分文件后再导入。")
            buf = f"{buf}\n{piece}" if buf else piece
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
        total_pages = len(reader.pages)
        for page_no, page in enumerate(reader.pages, 1):
            try:
                text = _clean(page.extract_text() or "")
            except Exception:
                text = ""
            if not text:
                warnings.append(f"第 {page_no} 页没有可读取的文字，可能是扫描页或空白页。")
                continue
            for para_no, paragraph in enumerate((p.strip() for p in re.split(r"\n\s*\n", text)), 1):
                if paragraph:
                    entries.append((paragraph, page_no, para_no))
        if total_pages and len(warnings) == total_pages:
            raise _MaterialInputError("PDF 中没有可读取的文字；当前不支持对扫描版 PDF 进行 OCR。")
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
                path = Path(info.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("DOCX 压缩包包含不安全的路径。")
            if "word/document.xml" not in archive.namelist():
                raise ValueError("DOCX 文件无效：缺少正文内容。")
            root = ET.fromstring(archive.read("word/document.xml"))
    except ValueError:
        raise
    except _MaterialInputError:
        raise
    except Exception as exc:
        raise ValueError("DOCX 文件损坏或无法读取，请检查文件后重试。") from exc

    # Iterate paragraphs in XML order, including paragraphs nested inside tables.
    entries: list[tuple[str, int | None, int | None]] = []
    has_deletions = False
    para_no = 0
    for element in root.iter(f"{{{_DOCX_NS}}}p"):
        para_no += 1
        chunks = []
        deleted = {id(node) for revision in element.iter(f"{{{_DOCX_NS}}}del") for node in revision.iter()}
        for child in element.iter():
            if child.tag == f"{{{_DOCX_NS}}}del":
                has_deletions = True
            if child.tag == f"{{{_DOCX_NS}}}t" and child.text and id(child) not in deleted:
                chunks.append(child.text)
            elif child.tag == f"{{{_DOCX_NS}}}delText":
                has_deletions = True
            elif child.tag == f"{{{_DOCX_NS}}}tab":
                chunks.append("\t")
            elif child.tag == f"{{{_DOCX_NS}}}br":
                chunks.append("\n")
        text = _clean("".join(chunks))
        if text:
            entries.append((text, None, para_no))
    sources = _split_entries(entries, "docx")
    if not sources:
        raise ValueError("DOCX 中没有可读取的文字。")
    warnings = ["已忽略 DOCX 中标记为删除的修订内容。"] if has_deletions else []
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
