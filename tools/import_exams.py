#!/usr/bin/env python
"""Import local 408 exam PDFs into the private exam database without printing question text."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from study_assistant import ExamService

YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")
ANSWER_PATTERN = re.compile(r"答案|解析|参考答案", re.IGNORECASE)
COMBINED_RECALL_PATTERN = re.compile(r"真题及答案|试题及答案")


def detect_year(path: Path) -> int | None:
    match = YEAR_PATTERN.search(path.name)
    if match:
        return int(match.group())
    for parent in path.parents:
        match = YEAR_PATTERN.search(parent.name)
        if match:
            return int(match.group())
    return None


def is_answer(path: Path) -> bool:
    if COMBINED_RECALL_PATTERN.search(path.stem):
        return False
    return bool(ANSWER_PATTERN.search(path.stem))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="包含本地原卷和答案PDF的目录")
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="数据库目录；默认使用 CSA_DATA_DIR 或项目 data 目录",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"目录不存在：{root}")

    pdfs = sorted(root.rglob("*.pdf"))
    if not pdfs:
        print("未找到PDF文件。", file=sys.stderr)
        return 2

    service = ExamService(args.data_dir)
    papers = [path for path in pdfs if not is_answer(path)]
    answers = [path for path in pdfs if is_answer(path)]
    imported_by_year = {}
    stats = {"papers": 0, "duplicates": 0, "answers": 0, "warnings": 0, "failed": 0}

    for path in papers:
        year = detect_year(path)
        recall = bool(COMBINED_RECALL_PATTERN.search(path.stem)) or "回忆" in path.stem
        try:
            result = service.import_pdf(
                path.name,
                path.read_bytes(),
                year=year,
                source_kind="recall" if recall else "paper",
            )
            stats["papers"] += 1
            stats["duplicates"] += int(result.duplicate)
            stats["warnings"] += len(result.warnings)
            if year is not None:
                imported_by_year[year] = result.paper.id
            label = "回忆版" if recall else "原卷"
            print(
                f"{year or '年份未知'} {label}: {result.paper.filename}；"
                f"页数 {result.paper.page_count}，识别题数 {result.paper.question_count}，"
                f"重复 {result.duplicate}，提示 {len(result.warnings)} 条"
            )
            for warning in result.warnings:
                print(f"  提示：{warning}")
        except (ValueError, RuntimeError, KeyError, OSError, TypeError) as exc:
            stats["failed"] += 1
            print(f"导入失败 {path.name}: {exc}", file=sys.stderr)

    for path in answers:
        year = detect_year(path)
        paper_id = imported_by_year.get(year)
        if paper_id is None:
            print(f"未找到 {year or '未知年份'} 对应的原卷，跳过答案：{path.name}")
            continue
        try:
            warnings = service.attach_answer_pdf(paper_id, path.name, path.read_bytes())
            stats["answers"] += 1
            print(f"答案已关联：{year} {path.name}；提示 {len(warnings)} 条")
            for warning in warnings:
                print(f"  提示：{warning}")
        except (ValueError, RuntimeError, KeyError, OSError, TypeError) as exc:
            stats["failed"] += 1
            print(f"答案关联失败 {path.name}: {exc}", file=sys.stderr)

    print(
        "完成：原卷 {papers}，重复 {duplicates}，关联答案 {answers}，提示 {warnings}，失败 {failed}".format(
            **stats
        )
    )
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
