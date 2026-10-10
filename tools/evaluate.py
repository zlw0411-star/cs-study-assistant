#!/usr/bin/env python
"""Evaluate retrieval/generation against the hand-maintained OS notes set.

Dense/hybrid runs use the production BGE embedder. Test doubles are never imported here.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from study_assistant import BGEEmbedder, StudyAssistant


def _installed(name):
    try:
        importlib.metadata.version(name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


def _model_revision(embedder):
    model = getattr(embedder, "_model", None)
    if model is None:
        return None


class _CaptureGenerator:
    """Capture the raw structured output before StudyAssistant validates citations."""
    def __init__(self, delegate):
        self.delegate = delegate
        self.last_payload = None

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def generate(self, question, hits):
        self.last_payload = None
        result = self.delegate.generate(question, hits)
        self.last_payload = result
        return result


def _sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        return model._first_module().auto_model.config._commit_hash
    except Exception:
        return None


def _subset_refusal_rate(rows, answerable):
    subset = [r for r in rows if r["answerable"] is answerable and r["generation_attempted"] and r["generation_success"] is True and r["refused"] is not None]
    return sum(r["refused"] is True for r in subset) / len(subset) if subset else None


def _system_no_hit_refusal_rate(rows):
    subset = [r for r in rows if not r["answerable"]]
    return sum(not r["hits"] for r in subset) / len(subset) if subset else None


def _system_no_hit_rate(rows, answerable):
    subset = [r for r in rows if r["answerable"] is answerable]
    return sum(not r["hits"] for r in subset) / len(subset) if subset else None


def _model_refusal_rate(rows):
    subset = [r for r in rows if r["generation_attempted"] and r["generation_success"] is True and r["refused"] is not None]
    return sum(r["refused"] is True for r in subset) / len(subset) if subset else None


def p95(values):
    if not values:
        return None
    ordered = sorted(values)
    import math
    return ordered[max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))]


def load_rows(split):
    rows = [json.loads(line) for line in (ROOT / "evaluation" / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if split == "all" or r["split"] == split]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    parser.add_argument("--methods", default="bm25", help="comma-separated bm25,dense,hybrid")
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--allow-partial", action="store_true", help="exit successfully when a requested method cannot run")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "evaluation.jsonl")
    args = parser.parse_args()
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    if not methods or set(methods) - {"bm25", "dense", "hybrid"}:
        parser.error("--methods accepts bm25,dense,hybrid")
    rows = load_rows(args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    errors = {}
    startup_ms = {}
    ollama_digest = None
    generation_available = None
    generation_warmup_errors = {}
    generation_warmup_ms = {}

    # Isolated temporary corpus: no user data, no vector contamination across model signatures.
    with tempfile.TemporaryDirectory(prefix="csa-evaluation-") as temp:
        embedder = BGEEmbedder()
        service = StudyAssistant(Path(temp), embedder=embedder)
        service.generator = _CaptureGenerator(service.generator)
        for note in sorted((ROOT / "examples" / "notes").glob("*.txt")):
            service.import_document(note.name, note.read_bytes())
        needs_dense = any(method in {"dense", "hybrid"} for method in methods)
        if needs_dense:
            try:
                started = time.perf_counter()
                service.build_index()  # first call includes model warm-up; record separately
                warmup_ms = (time.perf_counter() - started) * 1000
            except Exception as exc:
                warmup_ms = None
                errors["dense_index"] = f"{type(exc).__name__}: {exc}"
                methods = [m for m in methods if m == "bm25"]
        else:
            warmup_ms = None
        # Prime the lexical tokenizer/ranker and dense query path; startup cost is kept separate.
        for method in methods:
            if rows:
                started = time.perf_counter()
                try:
                    service.search(rows[0]["question"], method=method, top_k=5)
                    startup_ms[method] = (time.perf_counter() - started) * 1000
                except Exception as exc:
                    errors[f"{method}_warmup"] = f"{type(exc).__name__}: {exc}"
                    startup_ms[method] = None
        if not args.skip_generation and rows:
            try:
                import httpx
                generator = service.generator
                tags = httpx.get(generator.base_url + "/api/tags", timeout=3).json()
                model_entry = next((m for m in tags.get("models", []) if m.get("name") == generator.model), None)
                if model_entry is None:
                    generation_available = False
                    errors["ollama_model_missing"] = f"Ollama did not list configured model {generator.model}"
                else:
                    generation_available = True
                    ollama_digest = model_entry.get("digest")
            except Exception as exc:
                generation_available = False
                errors["ollama_tags"] = f"{type(exc).__name__}: {exc}"
        for method in methods:
            if not args.skip_generation and rows and generation_available:
                # One real request warms the generator cache; this response is intentionally excluded.
                try:
                    t_warm = time.perf_counter()
                    warmup_result = service.ask(rows[0]["question"], method=method, top_k=5)
                    generation_warmup_ms[method] = warmup_result.generation_ms if warmup_result.hits else (time.perf_counter() - t_warm) * 1000
                    if warmup_result.error:
                        generation_warmup_errors[method] = warmup_result.error
                except Exception as exc:
                    generation_warmup_errors[method] = f"{type(exc).__name__}: {exc}"
            for row in rows:
                started = time.perf_counter()
                hits = service.search(row["question"], method=method, top_k=5)
                retrieval_wall_ms = (time.perf_counter() - started) * 1000
                filenames = [hit.chunk.filename for hit in hits]
                hit = any(name in row["gold_documents"] for name in filenames) if row["answerable"] else None
                coverage = (len(set(filenames) & set(row["gold_documents"])) / len(row["gold_documents"])) if row["gold_documents"] else None
                answer = None
                generation_ms = None
                generation_success = None
                refused = None
                citations_valid = None
                generation_error = None
                generation_warnings = []
                generation_attempted = False
                if not args.skip_generation:
                    if hits and generation_available is not False:
                        generation_attempted = True
                        t0 = time.perf_counter()
                        result = service.ask(row["question"], method=method, top_k=5, use_llm=True)
                        generation_ms = result.generation_ms
                        answer = result.answer
                        refused = result.refused if result.error is None else None
                        generation_success = result.error is None
                        generation_error = result.error
                        generation_warnings = result.warnings
                        citations_valid = (bool(result.citations) and all(type(n) is int and 1 <= n <= len(result.hits) for n in result.citations)) if result.error is None and not result.refused else None
                        raw_generator_payload = service.generator.last_payload
                    elif hits and generation_available is False:
                        generation_error = errors.get("ollama_tags") or errors.get("ollama_model_missing") or "生成模型不可用，未测量。"
                        generation_warnings = ["模型不可用；逐题生成未运行。"]
                        refused = None
                    else:
                        # The assistant's no-hit refusal is a system decision, not model generation.
                        no_hit_result = service.ask(row["question"], method=method, top_k=5, use_llm=True)
                        answer = no_hit_result.answer
                        refused = no_hit_result.refused
                records.append({
                    "id": row["id"], "split": row["split"], "method": method,
                    "question": row["question"], "answerable": row["answerable"],
                    "gold_documents": row["gold_documents"], "gold_keywords": row["gold_keywords"],
                    "retrieved_documents": filenames,
                    "hits": [{"rank": h.rank, "score": h.score, "document": h.chunk.filename, "text": h.chunk.text,
                              "page": h.chunk.page, "start_line": h.chunk.start_line, "end_line": h.chunk.end_line} for h in hits],
                    "hit_at_5": hit, "multi_source_coverage": coverage,
                    "retrieval_ms": retrieval_wall_ms, "generation_ms": generation_ms,
                    "generation_success": generation_success, "citations": result.citations if not args.skip_generation and generation_success else [],
                    "generation_attempted": generation_attempted,
                    "model_available": generation_available if not args.skip_generation else None,
                    "citations_valid": citations_valid, "refused": refused, "answer": answer,
                    "generation_error": generation_error, "generation_warnings": generation_warnings,
                    "raw_generator_payload": raw_generator_payload if generation_attempted else None,
                })

    args.output.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in records), encoding="utf-8")
    summary = {
        "split": args.split, "methods_requested": args.methods.split(","),
        "methods_measured": sorted({r["method"] for r in records}), "question_count": len(rows),
        "generation_requested": not args.skip_generation,
        "generation_available": generation_available if not args.skip_generation else None,
        "generation_warmup_errors_by_method": generation_warmup_errors,
        "generation_warmup_ms_by_method": generation_warmup_ms,
        "dense_warmup_ms": warmup_ms,
        "startup_warmup_ms_by_method": startup_ms,
        "errors": errors, "python": sys.version.split()[0], "platform": platform.platform(),
        "package_versions": {name: importlib.metadata.version(name) for name in ("numpy", "jieba", "rank-bm25", "pypdf", "streamlit", "sentence-transformers", "httpx") if _installed(name)},
        "embedder": getattr(embedder, "signature", "unknown"),
        "embedder_revision": getattr(embedder, "revision", None),
        "embedder_resolved_revision": _model_revision(embedder),
        "generation_model": getattr(service.generator, "model", None) if not args.skip_generation else None,
        "generation_model_digest": ollama_digest,
        "generation_model_source": os.getenv("CSA_OLLAMA_MODEL_SOURCE", "local Ollama tag; source provenance is not exposed by the API") if not args.skip_generation else None,
        "generation_options": {"temperature": 0, "num_ctx": 8192, "num_predict": 768} if not args.skip_generation else None,
        "source_sha256": {name: _sha256_file(ROOT / "study_assistant" / f"{name}.py") for name in ("core", "embedding", "generation")},
        "evaluation_data_sha256": hashlib.sha256((ROOT / "evaluation" / "questions.jsonl").read_bytes()).hexdigest(),
        "notes_sha256": hashlib.sha256(b"".join(p.read_bytes() for p in sorted((ROOT / "examples" / "notes").glob("*.txt")))).hexdigest(),
        "metrics_by_method": {},
        "manual_scoring_required": True,
    }
    for method in sorted({r["method"] for r in records}):
        group = [r for r in records if r["method"] == method]
        answerable = [r for r in group if r["answerable"]]
        answerable_multi = [r for r in answerable if len(r["gold_documents"]) > 1]
        gen = [r for r in group if r["generation_success"] is not None]
        citations = [r for r in gen if r["citations_valid"] is not None]
        summary["metrics_by_method"][method] = {
            "hit_at_5": sum(r["hit_at_5"] is True for r in answerable) / len(answerable) if answerable else None,
            "answerable_count": len(answerable),
            "multi_source_coverage": statistics.mean(r["multi_source_coverage"] for r in answerable_multi) if answerable_multi else None,
            "multi_source_question_count": len(answerable_multi),
            "retrieval_ms_mean": statistics.mean(r["retrieval_ms"] for r in group) if group else None,
            "retrieval_ms_p95": p95([r["retrieval_ms"] for r in group]),
            "generation_success_rate": sum(r["generation_success"] is True for r in gen) / len(gen) if gen else None,
            "valid_citation_rate": sum(r["citations_valid"] is True for r in citations) / len(citations) if citations else None,
            "model_refusal_rate": _model_refusal_rate(group),
            "correct_refusal_rate_unanswerable": _subset_refusal_rate(group, answerable=False),
            "over_refusal_rate_answerable": _subset_refusal_rate(group, answerable=True),
            "system_no_hit_refusal_rate_unanswerable": _system_no_hit_refusal_rate(group),
            "system_no_hit_rate_answerable": _system_no_hit_rate(group, answerable=True),
            "manual_answer_correctness": None,
            "manual_citation_support": None,
        }
    summary_path = args.output.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(records)} rows to {args.output}")
    print(f"Summary: {summary_path}")
    if errors:
        print("Some requested methods were unavailable; see summary errors.", file=sys.stderr)
        if not args.allow_partial:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
