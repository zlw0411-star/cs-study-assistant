from dataclasses import dataclass, field


@dataclass
class PaperInfo:
    id: str
    filename: str
    year: int | None
    source_kind: str
    imported_at: str
    question_count: int
    page_count: int
    answer_filename: str | None
    answer_page_count: int


@dataclass
class QuestionInfo:
    id: str
    paper_id: str
    year: int | None
    number: int
    kind: str
    text: str
    start_page: int
    end_page: int
    subject: str
    topic_ids: list[str]
    matched_keywords: list[str]
    needs_review: bool
    classification_manual: bool
    status: str
    is_wrong: bool
    notes: str
    has_visual: bool


@dataclass
class ExamImportResult:
    paper: PaperInfo
    duplicate: bool
    warnings: list[str] = field(default_factory=list)
