from dataclasses import dataclass, field


@dataclass
class DocumentInfo:
    id: str
    filename: str
    media_type: str
    imported_at: str
    chunk_count: int


@dataclass
class Chunk:
    id: str
    document_id: str
    filename: str
    text: str
    page: int | None
    start_line: int | None
    end_line: int | None
    heading: str


@dataclass
class SearchHit:
    chunk: Chunk
    score: float
    rank: int


@dataclass
class ImportResult:
    document: DocumentInfo
    duplicate: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class AnswerResult:
    answer: str
    hits: list[SearchHit]
    citations: list[int]
    warnings: list[str]
    refused: bool
    error: str | None
    retrieval_ms: float
    generation_ms: float
