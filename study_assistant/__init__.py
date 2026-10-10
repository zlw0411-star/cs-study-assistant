"""A small, local-first assistant for Chinese computer-science study material."""

from .core import StudyAssistant
from .embedding import BGEEmbedder
from .exam_types import ExamImportResult, PaperInfo, QuestionInfo
from .exams import ExamService
from .generation import OllamaGenerator
from .types import AnswerResult, Chunk, DocumentInfo, ImportResult, SearchHit

__all__ = [
    "AnswerResult",
    "BGEEmbedder",
    "Chunk",
    "DocumentInfo",
    "ExamImportResult",
    "ExamService",
    "ImportResult",
    "OllamaGenerator",
    "PaperInfo",
    "QuestionInfo",
    "SearchHit",
    "StudyAssistant",
]
