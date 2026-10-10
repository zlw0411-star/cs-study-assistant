import sys
import json
import numpy as np

from tools import evaluate


def test_requested_dense_failure_returns_nonzero_unless_partial(tmp_path, monkeypatch):
    class UnavailableEmbedder:
        signature = "unavailable-test-model"

        def encode(self, texts, *, is_query=False):
            raise RuntimeError("model intentionally unavailable in this test")

    monkeypatch.setattr(evaluate, "BGEEmbedder", UnavailableEmbedder)
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--split", "dev", "--methods", "dense", "--skip-generation", "--output", str(tmp_path / "failed.jsonl")])
    assert evaluate.main() == 2
    assert (tmp_path / "failed.summary.json").exists()
    assert "dense_index" in (tmp_path / "failed.summary.json").read_text(encoding="utf-8")


def test_requested_dense_failure_can_be_explicitly_partial(tmp_path, monkeypatch):
    class UnavailableEmbedder:
        signature = "unavailable-test-model"

        def encode(self, texts, *, is_query=False):
            raise RuntimeError("model intentionally unavailable in this test")

    monkeypatch.setattr(evaluate, "BGEEmbedder", UnavailableEmbedder)
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--split", "dev", "--methods", "dense", "--skip-generation", "--allow-partial", "--output", str(tmp_path / "partial.jsonl")])
    assert evaluate.main() == 0


def test_online_model_warmup_citation_error_does_not_skip_question_generation(tmp_path, monkeypatch):
    from study_assistant import StudyAssistant

    class TestEmbedder:
        signature = "test-only-evaluation-embedder"
        def encode(self, texts, *, is_query=False): return np.ones((len(texts), 4), dtype=np.float32)

    class TagsResponse:
        def json(self): return {"models": [{"name": "test-model", "digest": "test-digest"}]}

    class ScriptedGenerator:
        model = "test-model"
        base_url = "http://test.invalid"
        def __init__(self): self.calls = 0
        def generate(self, question, hits):
            self.calls += 1
            if self.calls == 1:  # simulate warmup output rejected only for a citation mismatch
                return {"answer": "uncited claim", "citations": [1], "refused": False}
            return {"answer": "supported claim [1]", "citations": [1], "refused": False}

    generator = ScriptedGenerator()
    monkeypatch.setattr(evaluate, "BGEEmbedder", TestEmbedder)
    monkeypatch.setattr(evaluate, "StudyAssistant", lambda data_dir, embedder: StudyAssistant(data_dir, embedder=embedder, generator=generator))
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: TagsResponse())
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--split", "dev", "--methods", "bm25", "--output", str(tmp_path / "online.jsonl")])
    assert evaluate.main() == 0
    summary = json.loads((tmp_path / "online.summary.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (tmp_path / "online.jsonl").read_text(encoding="utf-8").splitlines()]
    assert summary["generation_available"] is True
    assert "bm25" in summary["generation_warmup_errors_by_method"]
    assert rows[0]["generation_attempted"] and rows[0]["generation_success"] is True
    assert rows[0]["model_available"] is True
    assert rows[0]["raw_generator_payload"]["citations"] == [1]
