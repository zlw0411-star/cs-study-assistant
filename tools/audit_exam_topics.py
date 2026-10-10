#!/usr/bin/env python
"""Write a private, text-free topic-classification review sample.

The output intentionally contains only year/question number and assigned topic
metadata. It never copies question text, answers, page images, or PDF bytes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

SAMPLE_YEARS = (2023, 2024, 2025)
SAMPLE_NUMBERS = (1, 5, 10, 13, 17, 22, 24, 28, 33, 36, 41, 46)

# These are audit prompts / known coverage checks, not labels used by the system
# and not gold answers. The sample can be reviewed later without exposing text.
AUDIT_FOCUS = {
    (2023, 1): "顺序表操作；留意线性表和排序/复杂度关键词竞争。",
    (2023, 13): "short整数表示；应检查整型转换/补码卡是否覆盖。",
    (2023, 24): "中断向量表的数据结构问题；考查跨学科主题和科目版式提示。",
    (2023, 36): "以太网冲突退避；检查介质访问控制卡。",
    (2024, 13): "机器指令、微指令与伪指令区别；检查指令系统边界。",
    (2024, 17): "TLB组相联和页式地址；跨组成原理/操作系统，需人工核对。",
    (2024, 22): "DMA数据通路；检查I/O控制与组成实现的交界。",
    (2024, 28): "线程共享文件描述符；进程线程与文件系统可能多考点。",
    (2024, 33): "网络吞吐量；确认目前目录是否有恰当的性能/交换专题。",
    (2024, 36): "无线CSMA/CA；检查介质访问卡，不应误分到有线CSMA/CD。",
    (2025, 5): "哈夫曼编码；知识点归数据结构，避免误归组成原理。",
    (2025, 13): "IEEE 754单精度；检查浮点表示卡。",
    (2025, 24): "虚拟化/VMM；与虚拟内存术语容易混淆。",
    (2025, 33): "电路、报文、分组交换的时延比较；检查时延拆分卡。",
    (2025, 41): "数组最优子段/状态设计；检查动态规划覆盖。",
    (2025, 46): "虚拟地址空间与运行时堆；检查OS存储管理边界。",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/exam.sqlite3"))
    parser.add_argument(
        "--output", type=Path, default=Path("data/private/exam-topic-review.json")
    )
    args = parser.parse_args()
    db_path = args.database.resolve()
    if not db_path.is_file():
        parser.error(f"数据库不存在：{db_path}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = []
    for year in SAMPLE_YEARS:
        for number in SAMPLE_NUMBERS:
            row = db.execute(
                """SELECT q.year,q.number,q.subject,q.topic_ids_json,q.needs_review,
                          p.source_kind
                   FROM questions q JOIN papers p ON p.id=q.paper_id
                   WHERE q.year=? AND q.number=? AND p.source_kind='paper'
                   ORDER BY p.filename LIMIT 1""",
                (year, number),
            ).fetchone()
            if row is None:
                continue
            rows.append(
                {
                    "year": int(row["year"]),
                    "question_number": int(row["number"]),
                    "source_kind": row["source_kind"],
                    "suggested_subject": row["subject"],
                    "suggested_topic_ids": json.loads(row["topic_ids_json"]),
                    "needs_review": bool(row["needs_review"]),
                    "audit_focus": AUDIT_FOCUS.get(
                        (int(row["year"]), int(row["number"])),
                        "抽查目录关键词与相邻考点是否匹配；需结合原卷页核对。",
                    ),
                    "human_verified": False,
                    "used_as_gold": False,
                }
            )
    db.close()
    distribution = Counter(row["suggested_subject"] for row in rows)
    payload = {
        "schema_version": 1,
        "notice": "AI辅助开发阶段抽查记录；不是人工标注、不是gold准确率，也未使用被测系统生成答案。输出不含题干、答案、PDF正文或图像。",
        "sample_rule": {
            "years": list(SAMPLE_YEARS),
            "question_numbers_per_year": list(SAMPLE_NUMBERS),
            "intended_count": len(SAMPLE_YEARS) * len(SAMPLE_NUMBERS),
        },
        "sampled_count": len(rows),
        "suggested_subject_distribution": dict(sorted(distribution.items())),
        "items": rows,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {len(rows)} 条不含正文的抽样记录：{args.output}")
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
