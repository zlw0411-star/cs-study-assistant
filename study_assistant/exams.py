from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .exam_types import ExamImportResult, PaperInfo, QuestionInfo

MAX_PDF_BYTES = 20 * 1024 * 1024
SUBJECTS = {"数据结构", "计算机组成原理", "操作系统", "计算机网络", "待分类"}
QUESTION_HEADER = re.compile(
    r"(?m)(?:^[ \t]*|(?<=选择题)[ \t]*)(?P<number>\d{1,2})[ \t]*[.．、:：][ \t]*"
)
VISUAL_HINTS = (
    "题图",
    "如图",
    "见图",
    "图中",
    "下图",
    "上图",
    "表中",
    "如下表",
    "见表",
    "电路图",
    "流程图",
    "波形图",
    "状态图",
)
CODE_HINT = re.compile(
    r"(?m)(?:\b(?:int|char|void|return|for|while|if|else|struct|class)\b|:=|->|\{[^}]{0,80}\})"
)


def _safe_filename(filename: str) -> str:
    name = str(filename).replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(" .")
    if not name or name in {".", ".."}:
        raise ValueError("文件名无效。")
    return name[:180]


def _sanitize_surrogates(text: str) -> tuple[str, int]:
    out: list[str] = []
    replaced = 0
    i = 0
    while i < len(text):
        code = ord(text[i])
        if 0xD800 <= code <= 0xDBFF and i + 1 < len(text):
            low = ord(text[i + 1])
            if 0xDC00 <= low <= 0xDFFF:
                out.append(chr(0x10000 + ((code - 0xD800) << 10) + low - 0xDC00))
                i += 2
                continue
        if 0xD800 <= code <= 0xDFFF:
            out.append("\ufffd")
            replaced += 1
        else:
            out.append(text[i])
        i += 1
    return "".join(out), replaced


def _page_texts(data: bytes, filename: str) -> tuple[list[str], list[str]]:
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("单个PDF不能超过20 MB。")
    if not data.startswith(b"%PDF-"):
        raise ValueError("文件不是有效的PDF。")
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ValueError(
                        "PDF有密码保护，目前只支持无密码或空密码加密文件。"
                    )
            except ValueError:
                raise
            except ImportError as exc:
                raise RuntimeError(
                    "读取此加密PDF需要 cryptography。请运行 scripts/setup.ps1 安装项目依赖。"
                ) from exc
            except Exception as exc:
                raise ValueError("PDF有密码保护，目前不支持需要密码的文件。") from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"PDF损坏或无法读取：{exc}") from exc

    pages: list[str] = []
    warnings: list[str] = []
    readable = 0
    for index, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - a bad page must not abort the full import.
            text = ""
        text, replaced = _sanitize_surrogates(text)
        if replaced:
            warnings.append(
                f"第 {index} 页含 {replaced} 个非法Unicode字符，题目文本中已替换为�；原始PDF保持不变。"
            )
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = "\n".join(
            re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()
        )
        visible = len(re.sub(r"\s", "", text))
        if visible < 40:
            if text.strip():
                warnings.append(
                    f"第 {index} 页仅有 {visible} 个非空白字符，疑似扫描页或页码；未从该页抽取题目，请用原卷预览核对。"
                )
            else:
                warnings.append(
                    f"第 {index} 页没有可提取文字，请用原卷预览核对扫描内容。"
                )
            pages.append("")
        else:
            readable += visible
            pages.append(text)
    if readable == 0:
        raise ValueError(
            "原卷没有可提取文字；扫描型原卷暂不支持自动导入拆题，请改用文字型原卷。扫描型答案PDF可关联后逐页查看。"
        )
    return pages, warnings


def _clean_repeated_lines(pages: list[str]) -> list[str]:
    normalized = []
    for page in pages:
        lines = page.splitlines()
        normalized.append(lines)
    cleaned = []
    for page_number, lines in enumerate(normalized, 1):
        kept = []
        nonempty = [index for index, value in enumerate(lines) if value.strip()]
        first_nonempty = nonempty[0] if nonempty else -1
        last_nonempty = nonempty[-1] if nonempty else -1
        for line_index, line in enumerate(lines):
            value = line.strip()
            if not value:
                continue
            is_page_edge = line_index in {first_nonempty, last_nonempty}
            if is_page_edge and re.fullmatch(r"[-—–]?\s*\d{1,3}\s*[-—–]?", value):
                continue
            if is_page_edge and re.search(
                rf"第\s*{page_number}\s*页(?:\s*/\s*共\s*\d+\s*页)?", value
            ):
                continue
            if is_page_edge and re.fullmatch(
                r".*(?:计算机学科专业基础|全国硕士研究生招生考试).*第?\s*\d+\s*页.*",
                value,
            ):
                continue
            if is_page_edge and re.fullmatch(
                r"(?:全国硕士研究生招生考试)?\s*计算机学科专业基础.*", value
            ):
                continue
            kept.append(value)
        cleaned.append("\n".join(kept))
    return cleaned


def _question_blocks(pages: list[str]) -> tuple[list[dict], list[str]]:
    pages = _clean_repeated_lines(pages)
    parts: list[str] = []
    starts: list[int] = []
    offset = 0
    for page in pages:
        starts.append(offset)
        parts.append(page)
        offset += len(page) + 2
    combined = "\n\n".join(parts)
    candidates = []
    for match in QUESTION_HEADER.finditer(combined):
        number = int(match.group("number"))
        if not 1 <= number <= 47:
            continue
        page_index = 0
        # There are at most a few dozen pages, and this keeps the mapping simple.
        for idx, start in enumerate(starts):
            if start > match.start():
                break
            page_index = idx
        candidates.append(
            {
                "number": number,
                "start": match.start(),
                "page": page_index + 1,
            }
        )

    accepted: list[dict] = []
    last_number = 0
    for candidate in candidates:
        number = candidate["number"]
        if not accepted:
            accepted.append(candidate)
            last_number = number
            continue
        if number <= last_number:
            continue
        accepted.append(candidate)
        last_number = number

    warnings: list[str] = []
    found = {item["number"] for item in accepted}
    missing = [number for number in range(1, 48) if number not in found]
    if missing:
        page_list = sorted({str(item["page"]) for item in accepted})
        preview_pages = ", ".join(page_list[:8]) + ("等" if len(page_list) > 8 else "")
        warnings.append(
            "未识别到全部主问题号（"
            + ", ".join(map(str, missing))
            + f"）；已识别题目起始页包括 {preview_pages}，请用原卷预览核对。"
        )

    blocks = []
    for index, item in enumerate(accepted):
        end = (
            accepted[index + 1]["start"] if index + 1 < len(accepted) else len(combined)
        )
        raw_block = combined[item["start"] : end]
        text = raw_block.strip()
        end_offset = item["start"] + max(0, len(raw_block.rstrip()) - 1)
        end_page = 1
        for page_index, start in enumerate(starts):
            if start > end_offset:
                break
            end_page = page_index + 1
        if not text:
            continue
        blocks.append(
            {
                "number": item["number"],
                "text": text,
                "start_page": item["page"],
                "end_page": max(item["page"], end_page),
                "kind": "choice" if item["number"] <= 40 else "comprehensive",
            }
        )
    if not blocks:
        warnings.append(
            "提取到了文字但没有识别出题号1-47；原卷已保存，题目需人工核对，页面预览可查看原文。"
        )
    else:
        warnings.append(
            "题目边界由题号规则自动识别；请结合原卷页面预览核对题干、公式、代码、图表和跨页范围。"
        )
    return blocks, warnings


def _normalize(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE).casefold()


def _keyword_matches(text: str, keyword: str) -> bool:
    if re.fullmatch(r"[A-Za-z0-9_]+", keyword):
        escaped = re.escape(keyword)
        return (
            re.search(
                rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])", text, re.IGNORECASE
            )
            is not None
        )
    return _normalize(keyword) in _normalize(text)


class ExamService:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(
            data_dir
            or os.getenv("CSA_DATA_DIR")
            or Path(__file__).resolve().parents[1] / "data"
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "exam.sqlite3"
        self.catalog_path = (
            Path(__file__).resolve().parents[1] / "examples" / "knowledge_points.json"
        )
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
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS papers(
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    year INTEGER,
                    source_kind TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    sha256 TEXT UNIQUE NOT NULL,
                    raw BLOB NOT NULL,
                    page_count INTEGER NOT NULL,
                    answer_filename TEXT,
                    answer_sha256 TEXT,
                    answer_raw BLOB,
                    answer_page_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS questions(
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    year INTEGER,
                    number INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    start_page INTEGER NOT NULL,
                    end_page INTEGER NOT NULL,
                    subject TEXT NOT NULL,
                    topic_ids_json TEXT NOT NULL,
                    matched_keywords_json TEXT NOT NULL,
                    needs_review INTEGER NOT NULL,
                    classification_manual INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    is_wrong INTEGER NOT NULL,
                    notes TEXT NOT NULL,
                    has_visual INTEGER NOT NULL,
                    UNIQUE(paper_id, number)
                );
                CREATE INDEX IF NOT EXISTS questions_year_number ON questions(year, number);
                CREATE INDEX IF NOT EXISTS questions_paper ON questions(paper_id);
                CREATE TABLE IF NOT EXISTS topic_learning(
                    topic_id TEXT PRIMARY KEY,
                    principle_note TEXT NOT NULL DEFAULT '',
                    transfer_note TEXT NOT NULL DEFAULT '',
                    self_checked INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT
                );
                """
            )

    def _load_catalog(self) -> list[dict]:
        if not self.catalog_path.exists():
            raise RuntimeError(
                f"知识点目录不存在：{self.catalog_path}。请先恢复 examples/knowledge_points.json，避免将题目永久导入为待分类。"
            )
        try:
            value = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"知识点目录无法读取：{self.catalog_path}：{exc}"
            ) from exc
        topics = value.get("topics") if isinstance(value, dict) else None
        if not isinstance(topics, list):
            raise TypeError("知识点目录格式错误：需要顶层 topics 数组。")
        return [topic for topic in topics if isinstance(topic, dict)]

    def get_topics(self) -> list[dict]:
        return self._load_catalog()

    @staticmethod
    def _verified_layout_subject(
        filename: str,
        year: int | None,
        source_kind: str,
        question_number: int,
        complete: bool,
    ) -> str | None:
        if (
            not complete
            or "408" not in filename
            or source_kind != "paper"
            or year is None
            or not 2009 <= year <= 2025
        ):
            return None
        if year == 2009:
            ranges = (
                (1, 10, "数据结构"),
                (11, 22, "计算机组成原理"),
            )
        else:
            ranges = (
                (1, 11, "数据结构"),
                (12, 22, "计算机组成原理"),
            )
        ranges += (
            (23, 32, "操作系统"),
            (33, 40, "计算机网络"),
            (41, 42, "数据结构"),
            (43, 44, "计算机组成原理"),
            (45, 46, "操作系统"),
            (47, 47, "计算机网络"),
        )
        return next(
            (
                subject
                for start, end, subject in ranges
                if start <= question_number <= end
            ),
            None,
        )

    def _classify(
        self, text: str, subject_hint: str | None = None
    ) -> tuple[str, list[str], list[str], bool]:
        topic_evidence = []
        weak_single_hit = False
        for topic in self._load_catalog():
            subject = topic.get("subject")
            topic_id = topic.get("id")
            if subject not in SUBJECTS - {"待分类"} or not isinstance(topic_id, str):
                continue
            keywords = topic.get("keywords", [])
            if not isinstance(keywords, list):
                continue
            matched = []
            for keyword in keywords:
                if not isinstance(keyword, str):
                    continue
                if len(_normalize(keyword)) == 1 and keyword not in {"栈", "堆", "帧"}:
                    continue
                if _keyword_matches(text, keyword):
                    matched.append(keyword)
                    if len(_normalize(keyword)) == 1:
                        weak_single_hit = True
            if matched:
                topic_evidence.append((subject, topic_id, sorted(set(matched))))
        if not topic_evidence:
            return subject_hint or "待分类", [], [], True

        if subject_hint is not None:
            hinted = [item for item in topic_evidence if item[0] == subject_hint]
            if not hinted:
                return subject_hint, [], [], True
            topic_evidence = hinted

        subject_score: dict[str, int] = defaultdict(int)
        for subject, _, matched in topic_evidence:
            subject_score[subject] += sum(
                min(len(_normalize(word)), 12) for word in matched
            )
        ordered = sorted(subject_score.items(), key=lambda item: (-item[1], item[0]))
        best_subject, best_score = ordered[0]
        tied = len(ordered) > 1 and ordered[1][1] == best_score
        close_competing_evidence = (
            len(ordered) > 1 and ordered[1][1] >= best_score * 0.6
        )
        best_topics = [
            (topic_id, matched)
            for subject, topic_id, matched in topic_evidence
            if subject == best_subject
        ]
        best_topics.sort(
            key=lambda item: (
                -sum(min(len(_normalize(word)), 12) for word in item[1]),
                item[0],
            )
        )
        best_topic_score = sum(
            min(len(_normalize(word)), 12) for word in best_topics[0][1]
        )
        topic_scores = [
            sum(min(len(_normalize(word)), 12) for word in matched)
            for _, matched in best_topics
        ]
        close_topic_competition = (
            len(topic_scores) > 1 and topic_scores[1] >= best_topic_score * 0.6
        )
        topic_ids = [
            topic_id
            for topic_id, matched in best_topics
            if sum(min(len(_normalize(word)), 12) for word in matched)
            == best_topic_score
        ]
        matched_keywords = sorted(
            {word for subject, _, words in topic_evidence for word in words}
        )
        return (
            best_subject,
            topic_ids,
            matched_keywords,
            tied
            or close_competing_evidence
            or close_topic_competition
            or weak_single_hit
            or len(topic_ids) > 1,
        )

    @staticmethod
    def _has_visual(text: str) -> bool:
        return any(hint in text for hint in VISUAL_HINTS) or bool(
            CODE_HINT.search(text)
        )

    def _paper_info(self, db, row) -> PaperInfo:
        count = db.execute(
            "SELECT count(*) FROM questions WHERE paper_id=?", (row["id"],)
        ).fetchone()[0]
        return PaperInfo(
            row["id"],
            row["filename"],
            row["year"],
            row["source_kind"],
            row["imported_at"],
            count,
            row["page_count"],
            row["answer_filename"],
            row["answer_page_count"],
        )

    def import_pdf(
        self,
        filename: str,
        data: bytes,
        year: int | None = None,
        source_kind: str = "paper",
    ) -> ExamImportResult:
        name = _safe_filename(filename)
        if Path(name).suffix.lower() != ".pdf":
            raise ValueError("仅支持PDF文件。")
        if source_kind not in {"paper", "recall"}:
            raise ValueError("source_kind 只能是 paper 或 recall。")
        if year is None:
            match = re.search(r"(?:19|20)\d{2}", name)
            year = int(match.group()) if match else None
        if year is not None and not (1900 <= int(year) <= 2100):
            raise ValueError("年份范围无效。")
        data = bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        with self._connect() as db:
            existing = db.execute(
                "SELECT * FROM papers WHERE sha256=?", (digest,)
            ).fetchone()
            if existing:
                return ExamImportResult(self._paper_info(db, existing), True, [])

        pages, warnings = _page_texts(data, name)
        blocks, parse_warnings = _question_blocks(pages)
        warnings.extend(parse_warnings)
        if source_kind == "recall":
            warnings.append(
                "该文件标记为回忆版，可能夹带解析、缺字或未覆盖综合题；不能作为隐藏答案的诊断练习，请逐页预览核对。"
            )
        paper_id = uuid.uuid4().hex
        imported_at = datetime.now(UTC).isoformat(timespec="seconds")
        staged = []
        for block in blocks:
            subject_hint = self._verified_layout_subject(
                name,
                year,
                source_kind,
                block["number"],
                len(blocks) == 47
                and {item["number"] for item in blocks} == set(range(1, 48)),
            )
            subject, topic_ids, keywords, needs_review = self._classify(
                block["text"], subject_hint=subject_hint
            )
            staged.append(
                (
                    uuid.uuid4().hex,
                    paper_id,
                    year,
                    block["number"],
                    block["kind"],
                    block["text"],
                    block["start_page"],
                    block["end_page"],
                    subject,
                    json.dumps(topic_ids, ensure_ascii=False),
                    json.dumps(keywords, ensure_ascii=False),
                    int(needs_review),
                    0,
                    "new",
                    0,
                    "",
                    int(self._has_visual(block["text"])),
                )
            )
        with self._connect() as db:
            db.execute(
                "INSERT INTO papers(id,filename,year,source_kind,imported_at,sha256,raw,page_count) VALUES(?,?,?,?,?,?,?,?)",
                (
                    paper_id,
                    name,
                    year,
                    source_kind,
                    imported_at,
                    digest,
                    data,
                    len(pages),
                ),
            )
            db.executemany(
                "INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                staged,
            )
            row = db.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
            info = self._paper_info(db, row)
        if blocks:
            complete = len(blocks) == 47 and {item["number"] for item in blocks} == set(
                range(1, 48)
            )
            used_layout_prior = (
                complete
                and "408" in name
                and source_kind == "paper"
                and year is not None
                and 2009 <= year <= 2025
            )
            warnings.append(
                f"已识别 {len(blocks)} 道题目；科目/考点为规则建议。请逐题通过原卷预览核对题号、题干和起止页，不能仅凭解析文本确认题目完整。"
            )
            if used_layout_prior:
                warnings.append(
                    "科目建议使用已核验的完整408原卷题号分布作为先验；知识点仍由关键词建议，可能缺失或歧义，请人工核对。"
                )
        return ExamImportResult(info, False, warnings)

    def attach_answer_pdf(self, paper_id: str, filename: str, data: bytes) -> list[str]:
        name = _safe_filename(filename)
        if Path(name).suffix.lower() != ".pdf":
            raise ValueError("答案文件必须是PDF。")
        data = bytes(data)
        if len(data) > MAX_PDF_BYTES:
            raise ValueError("单个PDF不能超过20 MB。")
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted:
                try:
                    if not reader.decrypt(""):
                        raise ValueError(
                            "答案PDF有密码保护，目前只支持空密码加密文件。"
                        )
                except ValueError:
                    raise
                except ImportError as exc:
                    raise RuntimeError(
                        "读取加密答案需要 cryptography，请运行 scripts/setup.ps1。"
                    ) from exc
                except Exception as exc:
                    raise ValueError(
                        "答案PDF需要密码，目前不支持需要密码的文件。"
                    ) from exc
            page_count = len(reader.pages)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"答案PDF损坏或无法读取：{exc}") from exc
        digest = hashlib.sha256(data).hexdigest()
        with self._connect() as db:
            row = db.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
            if row is None:
                raise KeyError("找不到对应的真题卷。")
            warnings = []
            if row["answer_sha256"] and row["answer_sha256"] != digest:
                warnings.append("该试卷已有答案PDF；本次上传已替换原关联答案。")
            db.execute(
                "UPDATE papers SET answer_filename=?,answer_sha256=?,answer_raw=?,answer_page_count=? WHERE id=?",
                (name, digest, data, page_count, paper_id),
            )
        return warnings

    def list_papers(self) -> list[PaperInfo]:
        with self._connect() as db:
            return [
                self._paper_info(db, row)
                for row in db.execute(
                    "SELECT * FROM papers ORDER BY year DESC, filename"
                )
            ]

    def get_paper_document(self, paper_id: str, answer: bool = False):
        with self._connect() as db:
            row = db.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
            if row is None:
                raise KeyError("找不到该试卷。")
            if answer and row["answer_raw"] is None:
                raise KeyError("该试卷没有关联答案PDF。")
            return self._paper_info(db, row), bytes(
                row["answer_raw"] if answer else row["raw"]
            )

    def delete_paper(self, paper_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM papers WHERE id=?", (paper_id,))
            return cursor.rowcount > 0

    @staticmethod
    def _question_info(row) -> QuestionInfo:
        return QuestionInfo(
            row["id"],
            row["paper_id"],
            row["year"],
            row["number"],
            row["kind"],
            row["text"],
            row["start_page"],
            row["end_page"],
            row["subject"],
            json.loads(row["topic_ids_json"]),
            json.loads(row["matched_keywords_json"]),
            bool(row["needs_review"]),
            bool(row["classification_manual"]),
            row["status"],
            bool(row["is_wrong"]),
            row["notes"],
            bool(row["has_visual"]),
        )

    def list_questions(
        self,
        subject: str | None = None,
        topic_id: str | None = None,
        year: int | None = None,
        status: str | None = None,
        wrong_only: bool = False,
        needs_review_only: bool = False,
        paper_id: str | None = None,
        query: str = "",
    ) -> list[QuestionInfo]:
        if status is not None and status not in {"new", "review", "mastered"}:
            raise ValueError("status 必须是 new、review 或 mastered。")
        clauses = []
        params: list = []
        for column, value in (
            ("subject", subject),
            ("year", year),
            ("status", status),
            ("paper_id", paper_id),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        if wrong_only:
            clauses.append("is_wrong=1")
        if needs_review_only:
            clauses.append("needs_review=1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM questions" + where + " ORDER BY year DESC, number, id",
                params,
            ).fetchall()
        result = [self._question_info(row) for row in rows]
        if topic_id is not None:
            result = [item for item in result if topic_id in item.topic_ids]
        if query.strip():
            needle = _normalize(query)
            result = [
                item
                for item in result
                if needle
                in _normalize(item.text + item.notes + " ".join(item.matched_keywords))
            ]
        return result

    def get_question(self, question_id: str) -> QuestionInfo:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM questions WHERE id=?", (question_id,)
            ).fetchone()
            if row is None:
                raise KeyError("找不到该题目。")
            return self._question_info(row)

    def save_progress(
        self,
        question_id: str,
        status: str = "new",
        is_wrong: bool = False,
        notes: str = "",
    ) -> QuestionInfo:
        if status not in {"new", "review", "mastered"}:
            raise ValueError("status 必须是 new、review 或 mastered。")
        if not isinstance(is_wrong, bool):
            raise TypeError("is_wrong 必须是 bool。")
        if not isinstance(notes, str):
            raise TypeError("notes 必须是字符串。")
        notes, _ = _sanitize_surrogates(notes)
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE questions SET status=?,is_wrong=?,notes=? WHERE id=?",
                (status, int(is_wrong), notes, question_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("找不到该题目。")
            row = db.execute(
                "SELECT * FROM questions WHERE id=?", (question_id,)
            ).fetchone()
            return self._question_info(row)

    def set_classification(
        self, question_id: str, subject: str, topic_ids: list[str]
    ) -> QuestionInfo:
        if subject not in SUBJECTS:
            raise ValueError("未知科目。")
        if not isinstance(topic_ids, list) or any(
            not isinstance(item, str) for item in topic_ids
        ):
            raise TypeError("topic_ids 必须是字符串列表。")
        topics = {item.get("id"): item for item in self._load_catalog()}
        for topic_id in topic_ids:
            topic = topics.get(topic_id)
            if topic is None or topic.get("subject") != subject:
                raise ValueError(f"知识点 {topic_id} 不存在或与所选科目不匹配。")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE questions SET subject=?,topic_ids_json=?,needs_review=0,classification_manual=1 WHERE id=?",
                (
                    subject,
                    json.dumps(sorted(set(topic_ids)), ensure_ascii=False),
                    question_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError("找不到该题目。")
            row = db.execute(
                "SELECT * FROM questions WHERE id=?", (question_id,)
            ).fetchone()
            return self._question_info(row)

    def reclassify_automatic(self) -> int:
        """Refresh suggestions from the current catalog, preserving user edits."""
        with self._connect() as db:
            papers = db.execute("SELECT * FROM papers").fetchall()
            changed = 0
            for paper in papers:
                questions = db.execute(
                    "SELECT * FROM questions WHERE paper_id=? ORDER BY number",
                    (paper["id"],),
                ).fetchall()
                numbers = {row["number"] for row in questions}
                complete = len(questions) == 47 and numbers == set(range(1, 48))
                for row in questions:
                    if row["classification_manual"]:
                        continue
                    subject_hint = self._verified_layout_subject(
                        paper["filename"],
                        paper["year"],
                        paper["source_kind"],
                        row["number"],
                        complete,
                    )
                    subject, topic_ids, keywords, needs_review = self._classify(
                        row["text"], subject_hint=subject_hint
                    )
                    db.execute(
                        """UPDATE questions SET subject=?,topic_ids_json=?,
                        matched_keywords_json=?,needs_review=? WHERE id=?""",
                        (
                            subject,
                            json.dumps(topic_ids, ensure_ascii=False),
                            json.dumps(keywords, ensure_ascii=False),
                            int(needs_review),
                            row["id"],
                        ),
                    )
                    changed += 1
            return changed

    def refresh_source_locations(self) -> int:
        """Reparse stored PDFs to refresh extracted text and source page spans."""
        updated = 0
        with self._connect() as db:
            papers = db.execute("SELECT * FROM papers").fetchall()
            for paper in papers:
                pages, _ = _page_texts(bytes(paper["raw"]), paper["filename"])
                blocks, _ = _question_blocks(pages)
                by_number = {block["number"]: block for block in blocks}
                rows = db.execute(
                    "SELECT * FROM questions WHERE paper_id=?",
                    (paper["id"],),
                ).fetchall()
                for row in rows:
                    block = by_number.get(row["number"])
                    if block is None:
                        continue
                    db.execute(
                        """UPDATE questions SET text=?,start_page=?,end_page=?,
                        has_visual=? WHERE id=?""",
                        (
                            block["text"],
                            block["start_page"],
                            block["end_page"],
                            int(self._has_visual(block["text"])),
                            row["id"],
                        ),
                    )
                    updated += 1
        return updated

    def topic_stats(self, subject: str | None = None) -> list[dict]:
        topics = self._load_catalog()
        if subject is not None:
            topics = [topic for topic in topics if topic.get("subject") == subject]
        counts = defaultdict(lambda: Counter())
        for question in self.list_questions(subject=subject):
            for topic_id in question.topic_ids:
                stat = counts[topic_id]
                stat["total"] += 1
                stat[question.status] += 1
                if question.is_wrong:
                    stat["wrong"] += 1
                    if question.status != "mastered":
                        stat["unresolved_wrong"] += 1
        with self._connect() as db:
            learning = {
                row["topic_id"]: row
                for row in db.execute("SELECT * FROM topic_learning")
            }
        result = []
        for topic in topics:
            topic_id = topic.get("id")
            if not isinstance(topic_id, str):
                continue
            reflection = learning.get(topic_id)
            result.append(
                {
                    "topic_id": topic_id,
                    "total": counts[topic_id]["total"],
                    "new": counts[topic_id]["new"],
                    "review": counts[topic_id]["review"],
                    "mastered": counts[topic_id]["mastered"],
                    "wrong": counts[topic_id]["wrong"],
                    "unresolved_wrong": counts[topic_id]["unresolved_wrong"],
                    "self_checked": bool(reflection["self_checked"])
                    if reflection
                    else False,
                    "has_reflection": bool(
                        reflection
                        and (
                            reflection["principle_note"].strip()
                            or reflection["transfer_note"].strip()
                        )
                    ),
                }
            )
        return result

    def get_topic_progress(self, topic_id: str) -> dict:
        self._require_topic(topic_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM topic_learning WHERE topic_id=?", (topic_id,)
            ).fetchone()
        if row is None:
            return {
                "topic_id": topic_id,
                "principle_note": "",
                "transfer_note": "",
                "self_checked": False,
                "updated_at": None,
            }
        return dict(row)

    def save_topic_progress(
        self,
        topic_id: str,
        principle_note: str,
        transfer_note: str,
        self_checked: bool = False,
    ) -> dict:
        self._require_topic(topic_id)
        if not isinstance(principle_note, str) or not isinstance(transfer_note, str):
            raise TypeError("原理说明和迁移说明必须是字符串。")
        if not isinstance(self_checked, bool):
            raise TypeError("self_checked 必须是 bool。")
        principle_note, _ = _sanitize_surrogates(principle_note)
        transfer_note, _ = _sanitize_surrogates(transfer_note)
        if self_checked and not (principle_note.strip() and transfer_note.strip()):
            raise ValueError("自评检查前，请同时填写原理说明和迁移说明。")
        updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as db:
            db.execute(
                """INSERT INTO topic_learning
                (topic_id,principle_note,transfer_note,self_checked,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(topic_id) DO UPDATE SET
                principle_note=excluded.principle_note,
                transfer_note=excluded.transfer_note,
                self_checked=excluded.self_checked,
                updated_at=excluded.updated_at""",
                (
                    topic_id,
                    principle_note,
                    transfer_note,
                    int(self_checked),
                    updated_at,
                ),
            )
        return self.get_topic_progress(topic_id)

    def _require_topic(self, topic_id: str) -> dict:
        for topic in self._load_catalog():
            if topic.get("id") == topic_id:
                return topic
        raise KeyError(f"知识点不存在：{topic_id}")

    def render_page(self, paper_id: str, page: int, answer: bool = False) -> bytes:
        if type(page) is not int or page < 1:
            raise ValueError("页码必须是从1开始的正整数。")
        _, raw = self.get_paper_document(paper_id, answer=answer)
        try:
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(raw)
            if page > len(document):
                document.close()
                raise ValueError("页码超过PDF总页数。")
            pdf_page = document[page - 1]
            image = pdf_page.render(scale=1.5).to_pil()
            output = io.BytesIO()
            image.save(output, format="PNG")
            pdf_page.close()
            document.close()
            return output.getvalue()
        except ValueError:
            raise
        except ImportError as exc:
            raise RuntimeError(
                "缺少页面预览依赖，请运行 scripts/setup.ps1 安装 pypdfium2。"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"PDF页面渲染失败：{exc}") from exc
