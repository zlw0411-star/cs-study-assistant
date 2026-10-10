from pathlib import Path

import numpy as np
import pytest

from study_assistant import StudyAssistant


class FakeEmbedder:
    """Deterministic test double; it is not BGE and must never be reported as real dense evaluation."""
    signature = "test-only-hash-v1"

    def encode(self, texts, *, is_query=False):
        out = np.zeros((len(texts), 32), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in set(text.lower().split()):
                out[row, sum(token.encode("utf-8")) % out.shape[1]] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1)


class FakeGenerator:
    def __init__(self, answer=None, citations=None, refused=False, exc=None):
        self.answer = answer or "根据资料说明。[1]"
        self.citations = [1] if citations is None else citations
        self.refused = refused
        self.exc = exc

    def generate(self, question, hits):
        if self.exc:
            raise self.exc
        return {"answer": self.answer, "citations": self.citations, "refused": self.refused}


@pytest.fixture
def assistant(tmp_path):
    return StudyAssistant(tmp_path / "db", embedder=FakeEmbedder(), generator=FakeGenerator())


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()


@pytest.fixture
def notes_dir():
    return Path(__file__).resolve().parents[1] / "examples" / "notes"
