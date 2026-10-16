"""Offline contract tests for source-grounded organization of material analysis.

No material files or live services are used. The transport returns model-shaped
JSON, leaving batching, validation, reduction and report assembly to the analyzer.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from types import SimpleNamespace

import httpx
import pytest

from study_assistant.analysis import (
    MaterialAnalyzer,
    OnlineAnalysisClient,
    OnlineAnalysisError,
    OnlineModelConfig,
    _validate_map,
    _validate_report,
    _make_batches,
    MAP_BATCH_CHARS,
)
from study_assistant.material_types import MaterialSource


FIELDS = ("chapter", "definition", "key_points", "formulas", "conditions")
DETAILS = {
    "chapter": "第二章 栈",
    "definition": "栈是后进先出的线性表。",
    "key_points": ["只在栈顶操作"],
    "formulas": ["top = n - 1"],
    "conditions": ["入栈前检查容量"],
}


def point(ids, **changes):
    value = {"name": "栈", "explanation": "后进先出", "source_ids": list(ids)}
    value.update(deepcopy(DETAILS))
    value.update(changes)
    return value


def node(ids, title="第二章 栈", **changes):
    value = {"title": title, "summary": "栈的定义和操作", "source_ids": list(ids), "children": []}
    value.update(changes)
    return value


def mapped(ids, *, summary="局部摘要", outline=None, points=None):
    return {
        "coverage_source_ids": list(ids),
        "summary": summary,
        "outline": outline if outline is not None else [node(ids)],
        "knowledge_points": points if points is not None else [point(ids)],
    }


def final(ids, *, outline=None, points=None):
    return {
        "title": "学习报告", "material_type": "讲义/文档", "summary": "栈的学习资料",
        "outline": outline if outline is not None else [node(ids)],
        "knowledge_points": points if points is not None else [point(ids)],
        "learning_suggestions": ["练习入栈和出栈"], "warnings": [],
    }


def analyze(sources, responder, material=None):
    def handler(request):
        payload = json.loads(request.content)
        value = responder(payload)
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps(value, ensure_ascii=False)},
        }]})

    config = OnlineModelConfig("https://offline.invalid/v1", "mock-model", "offline-key")
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        client = OnlineAnalysisClient(config, transport)
        return MaterialAnalyzer(client).analyze(material, sources)


def two_stage(map_value, report_value):
    values = iter([map_value, report_value])
    return lambda _payload: deepcopy(next(values))


def validate(stage, value):
    # Preserve the pre-existing positional validator APIs; no new arguments assumed.
    if stage == "map":
        return _validate_map(value, {"S1"})
    return _validate_report(value, {"S1"}, 1, 1, "mock-model")


def test_long_source_split_preserves_lines_and_all_original_characters():
    formula = "条件 n > 0 时：T(n) = n + 1\n"
    text = "第一章\n" + "x" * (MAP_BATCH_CHARS - 20) + "\n" + formula + "尾文" * 4000
    batches = _make_batches([MaterialSource("S1", text, "page 1")])
    pieces = [source.text for batch in batches for source in batch]
    assert "".join(pieces) == text
    assert all(len(piece) <= MAP_BATCH_CHARS for piece in pieces)
    assert any(formula in piece for piece in pieces)
    assert all(source.id == "S1" for batch in batches for source in batch)


@pytest.mark.parametrize("shared", [False, True])
def test_chapterless_identical_points_need_shared_evidence_to_merge(shared):
    first = point(["S1"])
    second = point(["S1"] if shared else ["S2"])
    del first["chapter"]
    del second["chapter"]
    result = _validate_map(mapped(["S1", "S2"], points=[first, second]), {"S1", "S2"})
    assert len(result["knowledge_points"]) == (1 if shared else 2)


def test_outline_origin_defaults_and_is_grounded_in_its_own_raw_citations():
    sources = [
        MaterialSource("S1", "第二章 栈\n后进先出。", "page 1"),
        MaterialSource("S2", "第三章 队列\n先进先出。", "page 2"),
    ]
    outline = [
        node(["S1"], origin="original"),
        node(["S1"], title="第三章 队列", origin="original"),  # Only S2 contains it.
        node(["S2"], title="模型编造的章节", origin="original"),
        node(["S1"], title="归纳标题"),
        node(["S1"], title="父主题", origin="inferred", children=[
            node(["S2"], title="第三章 队列", origin="original"),
            node(["S2"], title="不存在的子标题", origin="original"),
        ]),
    ]
    result = analyze(sources, two_stage(
        mapped(["S1", "S2"], outline=outline),
        final(["S1", "S2"], outline=outline),
    ))
    assert [n["origin"] for n in result["outline"]] == [
        "original", "inferred", "inferred", "inferred", "inferred",
    ]
    assert [n["origin"] for n in result["outline"][-1]["children"]] == ["original", "inferred"]


@pytest.mark.parametrize("stage", ["map", "final"])
@pytest.mark.parametrize("origin", [None, 7, [], {}, "invented", ""])
def test_invalid_outline_origin_is_rejected(stage, origin):
    value = mapped(["S1"]) if stage == "map" else final(["S1"])
    value["outline"][0]["origin"] = origin
    with pytest.raises(OnlineAnalysisError):
        validate(stage, value)


@pytest.mark.parametrize("stage", ["map", "final"])
@pytest.mark.parametrize("details", [DETAILS, {
    "chapter": "", "definition": "", "key_points": [], "formulas": [], "conditions": [],
}, {}])
def test_optional_point_fields_survive_validation_and_accept_empty_values(stage, details):
    basic = {"name": "栈", "explanation": "后进先出", "source_ids": ["S1"]}
    basic.update(deepcopy(details))
    value = mapped(["S1"], points=[basic]) if stage == "map" else final(["S1"], points=[basic])
    normalized = validate(stage, value)["knowledge_points"][0]
    for key, expected in details.items():
        assert normalized[key] == expected


BAD_FIELDS = [
    ("chapter", None), ("chapter", []), ("chapter", 42), ("chapter", "x" * 6001),
    ("definition", None), ("definition", {}), ("definition", False), ("definition", "x" * 6001),
]
for _field in ("key_points", "formulas", "conditions"):
    BAD_FIELDS.extend((_field, value) for value in (
        None, "not a list", {}, [1], [None], [[]], ["x"] * 101, ["x" * 6001],
    ))


@pytest.mark.parametrize("stage", ["map", "final"])
@pytest.mark.parametrize("field,bad", BAD_FIELDS, ids=[f"invalid-{i}" for i in range(len(BAD_FIELDS))])
def test_optional_point_fields_reject_bad_types_and_oversized_values(stage, field, bad):
    value = mapped(["S1"]) if stage == "map" else final(["S1"])
    value["knowledge_points"][0][field] = bad
    with pytest.raises(OnlineAnalysisError):
        validate(stage, value)


def prompt_data(payload):
    """Read the trailing data array without depending on instruction wording."""
    user = next(m["content"] for m in payload["messages"] if m["role"] == "user")
    for index, char in enumerate(user):
        if char != "[":
            continue
        try:
            value = json.loads(user[index:])
        except ValueError:
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return user, value
    raise AssertionError("Expected a JSON data array in the prompt")


def test_forced_reduce_preserves_fields_in_map_reduce_and_final_prompts():
    sources = [MaterialSource(f"S{i}", "第二章 栈\n" + "x" * 9990, f"page {i}") for i in range(1, 6)]
    seen = {"map": 0, "reduce": 0, "final": 0}

    def responder(payload):
        user, data = prompt_data(payload)
        assert len(user) <= 24_000
        text = "\n".join(m["content"] for m in payload["messages"])
        for field in ("origin", *FIELDS):
            assert field in text, f"prompt dropped schema field {field}"
        if "text" in data[0]:
            seen["map"] += 1
            ids = [entry["id"] for entry in data]
            return mapped(ids, summary="a" * 5000, outline=[node(ids, origin="original")])
        for entry in data:
            assert entry["outline"][0]["origin"] == "original"
            for field, expected in DETAILS.items():
                assert entry["knowledge_points"][0][field] == expected
        ids = sorted({sid for entry in data for sid in entry["coverage_source_ids"]})
        if len(ids) < len(sources):
            seen["reduce"] += 1
            return mapped(ids, outline=[node(ids, origin="original")])
        seen["final"] += 1
        return final(ids, outline=[node(ids, origin="original")])

    result = analyze(sources, responder)
    assert seen["map"] == 5
    assert seen["reduce"] >= 1
    assert seen["final"] == 1
    assert result["outline"][0]["origin"] == "original"
    for field, expected in DETAILS.items():
        assert result["knowledge_points"][0][field] == expected


@pytest.mark.parametrize("stage", ["map", "reduce", "final"])
@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("oversized", [False, True], ids=["wrong-type", "oversized"])
def test_pipeline_rejects_invalid_new_fields_at_each_stage(stage, field, oversized):
    sources = [MaterialSource(f"S{i}", "x" * 10_000, f"page {i}") for i in range(1, 6)]
    reached = []

    def responder(payload):
        _user, data = prompt_data(payload)
        if "text" in data[0]:
            current = "map"
            ids = [entry["id"] for entry in data]
            value = mapped(ids, summary="a" * 5000)
        else:
            ids = sorted({sid for entry in data for sid in entry["coverage_source_ids"]})
            current = "reduce" if len(ids) < len(sources) else "final"
            value = mapped(ids) if current == "reduce" else final(ids)
        if current == stage:
            reached.append(current)
            if field in ("chapter", "definition"):
                bad = "x" * 6001 if oversized else []
            else:
                bad = ["x"] * 101 if oversized else "not a list"
            value["knowledge_points"][0][field] = bad
        return value

    with pytest.raises(OnlineAnalysisError):
        analyze(sources, responder)
    assert reached, f"The invalid {stage} response was never exercised"


def test_referenced_metadata_counts_recursive_citations_not_processing_coverage():
    ids = ["S1", "S2", "S3"]
    sources = [MaterialSource(sid, "栈的资料", sid) for sid in ids]
    outline = [node(["S1"], children=[node(["S2"])])]
    result = analyze(sources, two_stage(mapped(ids), final(ids, outline=outline, points=[point(["S1"])])))
    metadata = result["metadata"]
    assert metadata["analyzed_source_count"] == metadata["total_source_count"] == 3
    assert metadata["referenced_source_count"] == 2
    assert metadata["unreferenced_source_ids"] == ["S3"]
    basis = metadata["coverage_basis"]
    assert any(word in basis for word in ("处理", "分析范围", "处理范围"))
    assert "语义" in basis
    assert any(word in basis for word in ("不代表", "不保证", "不证明", "不等于", "不能证明", "不意味着"))
    warnings = "\n".join(result["warnings"])
    assert "S3" in warnings
    assert any(word in warnings for word in ("未引用", "未被引用", "未在", "没有引用"))


def test_all_sources_referenced_in_outline_or_points_have_no_missing_ids():
    ids = ["S1", "S2"]
    result = analyze([MaterialSource(sid, "资料", sid) for sid in ids], two_stage(
        mapped(ids), final(ids, outline=[node(["S1"])], points=[point(["S2"])]),
    ))
    assert result["metadata"]["referenced_source_count"] == 2
    assert result["metadata"]["unreferenced_source_ids"] == []


@pytest.mark.parametrize("count", [101, 137, 250])
def test_many_extraction_warnings_are_bounded_with_explicit_omitted_count(count):
    extraction = [f"提取提示-{index:04d}" for index in range(count)]
    material = SimpleNamespace(filename="memory.pdf", warnings=extraction)
    result = analyze([MaterialSource("S1", "栈", "page 1")], two_stage(
        mapped(["S1"]), final(["S1"]),
    ), material)
    warnings = result["warnings"]
    assert 1 <= len(warnings) <= 100
    assert len(warnings) == len(set(warnings))
    retained = [warning for warning in warnings if warning in extraction]
    assert retained
    omitted = count - len(retained)
    notices = [warning for warning in warnings if warning not in extraction]
    assert any(
        any(word in notice for word in ("截断", "省略", "未展示", "未显示", "忽略"))
        and str(omitted) in re.findall(r"\d+", notice)
        for notice in notices
    ), f"Missing explicit truncation count {omitted}: {notices}"


@pytest.mark.parametrize("difference", [None, "chapter", "definition", "key_points", "formulas", "conditions", "explanation"])
def test_duplicate_points_merge_only_when_content_and_chapter_are_compatible(difference):
    first = point(["S1"])
    second = point(["S2"])
    if difference:
        second[difference] = ["不同内容"] if isinstance(second[difference], list) else "不同内容"
    ids = ["S1", "S2"]
    result = analyze([MaterialSource(sid, "第二章 栈", sid) for sid in ids], two_stage(
        mapped(ids, points=[first, second]), final(ids, points=[first, second]),
    ))
    points = result["knowledge_points"]
    if difference is None:
        assert len(points) == 1
        assert set(points[0]["source_ids"]) == set(ids)
        for field, expected in DETAILS.items():
            assert points[0][field] == expected
    else:
        assert len(points) == 2
        assert {tuple(p["source_ids"]) for p in points} == {("S1",), ("S2",)}
        assert {json.dumps(p[difference], ensure_ascii=False) for p in points} == {
            json.dumps(first[difference], ensure_ascii=False),
            json.dumps(second[difference], ensure_ascii=False),
        }
