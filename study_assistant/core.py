from __future__ import annotations
import hashlib, os, re, sqlite3, time, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from .types import AnswerResult, Chunk, DocumentInfo, ImportResult, SearchHit
from .embedding import BGEEmbedder
from .generation import OllamaGenerator

MAX_BYTES = 20 * 1024 * 1024
CHUNK_SIZE, CHUNK_OVERLAP = 320, 60


def _safe_name(name: str) -> str:
    name = str(name).replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name).strip(" .")
    if not name or name in {".", ".."}:
        raise ValueError("文件名无效。")
    return name[:180]


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    raise ValueError("文本编码无法识别，请将文件另存为 UTF-8 或 GB18030。")


def _sanitize_surrogates(text: str) -> tuple[str, int]:
    """Convert valid UTF-16 surrogate pairs and replace malformed lone code points."""
    output = []
    replaced = 0
    i = 0
    while i < len(text):
        code = ord(text[i])
        if 0xD800 <= code <= 0xDBFF and i + 1 < len(text):
            low = ord(text[i + 1])
            if 0xDC00 <= low <= 0xDFFF:
                output.append(chr(0x10000 + ((code - 0xD800) << 10) + low - 0xDC00))
                i += 2
                continue
        if 0xD800 <= code <= 0xDFFF:
            output.append("\uFFFD")
            replaced += 1
        else:
            output.append(text[i])
        i += 1
    return "".join(output), replaced


def _make_chunks(doc_id: str, filename: str, text: str, page: int | None = None):
    lines = text.splitlines(keepends=True)
    if not lines and text:
        lines = [text]
    out = []
    pos = 0
    heading = ""
    while pos < len(text):
        end = min(len(text), pos + CHUNK_SIZE)
        if end < len(text):
            # Prefer a nearby paragraph/line boundary without producing tiny fragments.
            boundary = max(
                text.rfind("\n", pos + CHUNK_SIZE // 2, end),
                text.rfind("。", pos + CHUNK_SIZE // 2, end),
                text.rfind(" ", pos + CHUNK_SIZE // 2, end),
            )
            if boundary > pos:
                end = boundary + 1
        piece = text[pos:end].strip()
        if piece:
            first_line = text.count("\n", 0, pos) + 1
            last_line = max(
                first_line,
                text.count("\n", 0, end)
                + (1 if end and text[end - 1 : end] != "\n" else 0),
            )
            for line in piece.splitlines():
                m = re.match(r"^\s{0,3}#{1,6}\s+(.+)", line)
                if m:
                    heading = m.group(1).strip()
            out.append(
                Chunk(
                    uuid.uuid4().hex,
                    doc_id,
                    filename,
                    piece,
                    page,
                    first_line if page is None else None,
                    last_line if page is None else None,
                    heading,
                )
            )
        if end >= len(text):
            break
        pos = max(pos + 1, end - CHUNK_OVERLAP)
    return out


class StudyAssistant:
    def __init__(self, data_dir: Path | None = None, embedder=None, generator=None):
        self.data_dir = Path(
            data_dir
            or os.getenv("CSA_DATA_DIR")
            or Path(__file__).resolve().parents[1] / "data"
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "study_assistant.sqlite3"
        self.embedder = embedder or BGEEmbedder()
        self.generator = generator or OllamaGenerator()
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
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, filename TEXT NOT NULL, media_type TEXT NOT NULL, imported_at TEXT NOT NULL, sha256 TEXT UNIQUE NOT NULL, raw BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE, text TEXT NOT NULL, page INTEGER, start_line INTEGER, end_line INTEGER, heading TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(document_id);
            CREATE TABLE IF NOT EXISTS vectors(chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE, signature TEXT NOT NULL, vector BLOB NOT NULL, dimensions INTEGER NOT NULL);
            """)

    def _info(self, db, row):
        count = db.execute(
            "SELECT count(*) FROM chunks WHERE document_id=?", (row["id"],)
        ).fetchone()[0]
        return DocumentInfo(
            row["id"], row["filename"], row["media_type"], row["imported_at"], count
        )

    def import_document(self, filename: str, data: bytes) -> ImportResult:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("文件内容必须为 bytes。")
        data = bytes(data)
        if len(data) > MAX_BYTES:
            raise ValueError("单个文件不能超过 20 MB。")
        name = _safe_name(filename)
        suffix = Path(name).suffix.lower()
        warnings = []
        pages = []
        if suffix in {".md", ".txt"}:
            text = _decode(data)
            chunks = _make_chunks("", name, text)
            media = "text/markdown" if suffix == ".md" else "text/plain"
        elif suffix == ".pdf":
            try:
                from pypdf import PdfReader
                import io

                reader = PdfReader(io.BytesIO(data), strict=True)
                if reader.is_encrypted:
                    try:
                        if reader.decrypt("") == 0:
                            raise ValueError("PDF 已加密且需要密码，暂不支持导入。")
                    except Exception as exc:
                        raise ValueError(
                            "PDF 已加密且需要密码，暂不支持导入。"
                        ) from exc
                for i, p in enumerate(reader.pages, 1):
                    try:
                        page_text = (p.extract_text() or "").strip()
                    except Exception:
                        page_text = ""
                    page_text, replaced = _sanitize_surrogates(page_text)
                    if replaced:
                        warnings.append(
                            f"第 {i} 页含 {replaced} 个非法 Unicode 字符，索引文本中已替换为 �；原始 PDF 已原样保存。"
                        )
                    visible_count = len(re.sub(r"\s", "", page_text))
                    if visible_count >= 40:
                        pages.append((i, page_text))
                    elif page_text:
                        warnings.append(
                            f"第 {i} 页仅提取到少量文字（{visible_count} 个非空白字符），疑似扫描页或只有页码；已跳过，需 OCR 后再导入。"
                        )
                    else:
                        warnings.append(
                            f"第 {i} 页未提取到文字；扫描页需 OCR，已跳过。"
                        )
                if not pages:
                    raise ValueError(
                        "PDF 没有可提取文字，可能是扫描件；请先 OCR 后导入。"
                    )
                media = "application/pdf"
                chunks = []
                for page, txt in pages:
                    chunks.extend(_make_chunks("", name, txt, page))
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"PDF 损坏或无法读取：{exc}") from exc
        else:
            raise ValueError("仅支持 .md、.txt 和文字型 .pdf 文件。")
        if not chunks:
            warnings.append("文件中没有提取到可索引文本。")
        digest = hashlib.sha256(data).hexdigest()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        doc_id = uuid.uuid4().hex
        with self._connect() as db:
            existing = db.execute(
                "SELECT * FROM documents WHERE sha256=?", (digest,)
            ).fetchone()
            if existing:
                return ImportResult(self._info(db, existing), True, warnings)
            db.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?)",
                (doc_id, name, media, now, digest, data),
            )
            db.executemany(
                "INSERT INTO chunks VALUES(?,?,?,?,?,?,?)",
                [
                    (c.id, doc_id, c.text, c.page, c.start_line, c.end_line, c.heading)
                    for c in chunks
                ],
            )
        return ImportResult(
            DocumentInfo(doc_id, name, media, now, len(chunks)), False, warnings
        )

    def list_documents(self):
        with self._connect() as db:
            return [
                self._info(db, r)
                for r in db.execute("SELECT * FROM documents ORDER BY imported_at DESC")
            ]

    def delete_document(self, document_id: str) -> bool:
        with self._connect() as db:
            cur = db.execute("DELETE FROM documents WHERE id=?", (document_id,))
            return cur.rowcount > 0

    def get_document(self, document_id: str):
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            if row is None:
                raise KeyError("找不到该资料。")
            return self._info(db, row), bytes(row["raw"])

    def _sig(self):
        sig = getattr(self.embedder, "signature", None)
        if not isinstance(sig, str) or not sig:
            raise RuntimeError("Embedder 必须提供非空 signature。")
        return sig

    def build_index(self) -> int:
        sig = self._sig()
        with self._connect() as db:
            rows = db.execute(
                "SELECT c.id,c.text,v.signature FROM chunks c LEFT JOIN vectors v ON v.chunk_id=c.id ORDER BY c.rowid"
            ).fetchall()
        mismatches = [r for r in rows if r["signature"] and r["signature"] != sig]
        if mismatches:
            raise RuntimeError(
                "已有向量使用了不同的嵌入模型。请先删除资料后重新导入，或清空 data/study_assistant.sqlite3 后重建索引。"
            )
        pending = [r for r in rows if not r["signature"]]
        if not pending:
            return 0
        vectors = np.asarray(
            self.embedder.encode([r["text"] for r in pending], is_query=False),
            dtype=np.float32,
        )
        if (
            vectors.ndim != 2
            or vectors.shape[0] != len(pending)
            or vectors.shape[1] < 1
            or not np.isfinite(vectors).all()
        ):
            raise RuntimeError("Embedder 返回维度或数值无效，索引未保存。")
        norms = np.linalg.norm(vectors, axis=1)
        if np.any(norms < 1e-8):
            raise RuntimeError("Embedder 返回零向量，索引未保存。")
        vectors = (vectors / norms[:, None]).astype(np.float32)
        with self._connect() as db:
            for row, vec in zip(pending, vectors):
                vec = np.asarray(vec, dtype=np.float32)
                db.execute(
                    "INSERT OR REPLACE INTO vectors VALUES(?,?,?,?)",
                    (row["id"], sig, vec.tobytes(), len(vec)),
                )
        return len(pending)

    def index_status(self):
        with self._connect() as db:
            total = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
            indexed = db.execute("SELECT count(*) FROM vectors").fetchone()[0]
            signatures = [
                r[0] for r in db.execute("SELECT DISTINCT signature FROM vectors")
            ]
        return {
            "total_chunks": total,
            "indexed_chunks": indexed,
            "embedding_model": signatures[0]
            if len(signatures) == 1
            else ("mixed" if signatures else self._sig()),
        }

    def _chunks(self, document_ids=None):
        with self._connect() as db:
            query = "SELECT c.*,d.filename FROM chunks c JOIN documents d ON d.id=c.document_id"
            args = []
            if document_ids is not None:
                if not document_ids:
                    return []
                query += (
                    " WHERE c.document_id IN ("
                    + ",".join("?" for _ in document_ids)
                    + ")"
                )
                args = list(document_ids)
            return [
                Chunk(
                    r["id"],
                    r["document_id"],
                    r["filename"],
                    r["text"],
                    r["page"],
                    r["start_line"],
                    r["end_line"],
                    r["heading"],
                )
                for r in db.execute(query, args)
            ]

    @staticmethod
    def _tokens(text):
        try:
            import jieba
        except ImportError as exc:
            raise RuntimeError(
                "BM25 中文分词依赖 jieba 缺失，请运行 scripts/setup.ps1。"
            ) from exc
        return [x.strip().lower() for x in jieba.cut(text) if x.strip()]

    def search(
        self,
        query: str,
        method: str = "hybrid",
        top_k: int = 5,
        document_ids: list[str] | None = None,
    ):
        if not isinstance(query, str) or not query.strip():
            return []
        if method not in {"bm25", "dense", "hybrid"}:
            raise ValueError("检索方式必须是 bm25、dense 或 hybrid。")
        if top_k < 1:
            return []
        chunks = self._chunks(document_ids)
        if not chunks:
            return []
        byid = {c.id: c for c in chunks}
        ranked = {}
        if method in {"dense", "hybrid"}:
            sig = self._sig()
            with self._connect() as db:
                rows = db.execute(
                    "SELECT chunk_id,signature,vector,dimensions FROM vectors"
                ).fetchall()
            indexed = {r["chunk_id"]: r for r in rows if r["chunk_id"] in byid}
            if any(r["signature"] != sig for r in indexed.values()):
                raise RuntimeError("语义索引模型签名不匹配。请检查索引模型并重新构建。")
            missing = set(byid) - set(indexed)
            if missing:
                raise RuntimeError(
                    f"当前资料有 {len(missing)} 个片段尚未建立语义索引。请先点击“构建语义索引”。"
                )
            rows = list(indexed.values())
            if not rows:
                return []
            q = np.asarray(
                self.embedder.encode([query], is_query=True), dtype=np.float32
            )
            if q.ndim != 2 or q.shape[0] != 1 or not np.isfinite(q).all():
                raise RuntimeError("查询向量无效，无法进行语义检索。")
            for row in rows:
                v = np.frombuffer(row["vector"], dtype=np.float32)
                if len(v) != row["dimensions"] or len(v) != q.shape[1]:
                    raise RuntimeError("索引向量维度不一致，请重建语义索引。")
                ranked.setdefault("dense", []).append(
                    (row["chunk_id"], float(np.dot(q[0], v)))
                )
            ranked["dense"].sort(key=lambda x: x[1], reverse=True)
        if method in {"bm25", "hybrid"}:
            try:
                from rank_bm25 import BM25Plus
            except ImportError as exc:
                raise RuntimeError(
                    "BM25 检索依赖 rank-bm25 缺失，请运行 scripts/setup.ps1。"
                ) from exc
            tokenized = [self._tokens(c.text) for c in chunks]
            qtokens = self._tokens(query)
            qset = set(qtokens)
            model = BM25Plus(tokenized)
            scores = model.get_scores(qtokens)
            # BM25Plus has positive IDF on tiny/single-document corpora. Overlap gate
            # prevents its delta term from surfacing unrelated documents.
            vals = [
                (c.id, float(s))
                for c, ts, s in zip(chunks, tokenized, scores)
                if qset.intersection(ts)
            ]
            ranked["bm25"] = sorted(vals, key=lambda x: x[1], reverse=True)
        if method == "hybrid":
            scores = {}
            for source in ranked.values():
                for rank, (cid, _) in enumerate(source, 1):
                    scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
            ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        else:
            ordered = ranked[method]
        return [
            SearchHit(byid[cid], float(score), rank)
            for rank, (cid, score) in enumerate(ordered[:top_k], 1)
            if cid in byid
        ]

    def ask(
        self,
        query: str,
        method: str = "hybrid",
        top_k: int = 5,
        use_llm: bool = True,
        document_ids: list[str] | None = None,
    ):
        t = time.perf_counter()
        hits = self.search(query, method, top_k, document_ids)
        retrieval_ms = (time.perf_counter() - t) * 1000
        warnings = []
        citations = []
        refused = False
        error = None
        generation_ms = 0
        if not hits:
            return AnswerResult(
                "没有找到相关资料，当前资料不足以回答。",
                [],
                [],
                warnings,
                True,
                None,
                retrieval_ms,
                0,
            )
        if not use_llm:
            return AnswerResult(
                "以下是检索到的原文片段，请据此自行核对。",
                hits,
                [],
                warnings,
                False,
                None,
                retrieval_ms,
                0,
            )
        t = time.perf_counter()
        try:
            result = self.generator.generate(query, hits)
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("answer"), str)
                or not isinstance(result.get("citations"), list)
                or not isinstance(result.get("refused"), bool)
                or any(type(x) is not int for x in result.get("citations", []))
            ):
                raise ValueError("模型返回字段类型错误，无法验证答案。")
            answer = result["answer"]
            raw = result["citations"]
            refused = result["refused"]
            valid = set(range(1, len(hits) + 1))
            body = set(int(x) for x in re.findall(r"\[(\d+)\]", answer))
            declared = set(raw)
            if any(x not in valid for x in declared | body):
                raise ValueError("模型回答包含超出检索范围的引用编号。")
            if declared != body:
                raise ValueError("模型回答正文引用与 JSON citations 字段不一致。")
            citations = sorted(declared)
            if not citations and not refused:
                raise ValueError("模型没有提供有效引用，答案无法核验。")
        except Exception as exc:
            error = str(exc)
            warnings.append("生成或引用校验失败，已保留检索结果供查看原文。")
        generation_ms = (time.perf_counter() - t) * 1000
        return AnswerResult(
            answer if error is None else "生成未完成，请查看下方检索到的原文片段。",
            hits,
            citations,
            warnings,
            refused,
            error,
            retrieval_ms,
            generation_ms,
        )
