import json
from pathlib import Path


def test_fixed_dataset_has_exactly_30_questions_with_strict_splits():
    path = Path(__file__).resolve().parents[1] / "evaluation" / "questions.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 30 and len({r["id"] for r in rows}) == 30
    assert {r["split"] for r in rows} == {"dev", "test"}
    assert sum(bool(r["answerable"]) for r in rows) == 25
    assert sum(not r["answerable"] for r in rows) == 5
    note_names = {p.name for p in (path.parents[1] / "examples" / "notes").glob("*.txt")}
    for row in rows:
        assert set(row) >= {"id", "split", "question", "answerable", "gold_documents", "gold_keywords"}
        assert set(row["gold_documents"]) <= note_names
        if not row["answerable"]:
            assert row["gold_documents"] == []
    assert sum(r["split"] == "dev" for r in rows) == 20
    assert sum(r["split"] == "test" for r in rows) == 10


def test_test_questions_never_appear_in_dev_selection():
    rows = [json.loads(line) for line in (Path(__file__).resolve().parents[1] / "evaluation" / "questions.jsonl").read_text(encoding="utf-8").splitlines()]
    dev_ids = {r["id"] for r in rows if r["split"] == "dev"}
    test_ids = {r["id"] for r in rows if r["split"] == "test"}
    assert not dev_ids & test_ids
