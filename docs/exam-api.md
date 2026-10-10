# ExamService API

`ExamService` keeps exam papers and self-reported learning progress in a separate
`exam.sqlite3` database. It uses no language model. By default the data directory
is `CSA_DATA_DIR` when set, otherwise the project `data/` directory. Private PDFs
and extracted question text stay in the local database.

```python
from study_assistant import ExamService

service = ExamService()
result = service.import_pdf("2025 真题（原卷）.pdf", pdf_bytes, year=2025)
questions = service.list_questions(year=2025, subject="操作系统")
service.save_progress(questions[0].id, status="review", is_wrong=True)
preview_png = service.render_page(result.paper.id, page=1)
```

`import_pdf(filename, data, year=None, source_kind="paper")` stores the original
PDF bytes, deduplicates by SHA-256, extracts text, splits likely main questions,
and makes keyword-based topic suggestions from `examples/knowledge_points.json`.
`source_kind` is `paper` or `recall`. Text extraction cannot preserve every
diagram, formula, code layout, or multi-column detail. Warnings include low-text
pages and recognized question-number gaps; inspect each paper with `render_page`
before relying on the extracted question text. Recall editions may mix answers
into the questions or omit scanned sections, so they are not suitable as hidden-
answer diagnostic papers.

`attach_answer_pdf(paper_id, filename, data)` stores an answer PDF as-is. It does
not extract or generate answers; scanned answer PDFs can still be previewed with
`render_page(..., answer=True)`. `get_paper_document` returns `(PaperInfo, bytes)`;
requesting an absent answer raises `KeyError`. Deleting a paper cascades to its
questions and attached answer.

`QuestionInfo.status` is a user's self-rating: `new`, `review`, or `mastered`.
`mastered` means “I understand this question”; it does not claim knowledge-point
mastery. `is_wrong` is a separate
wrong-question-book flag. `topic_stats()` returns `wrong` for all flagged wrong
questions and `unresolved_wrong` for flagged questions whose self-rated status is
not `mastered`, so historical wrong answers can remain in the notebook without
always taking top priority.

Knowledge-point reflection is stored separately from individual question status.
`get_topic_progress(topic_id)` and `save_topic_progress(topic_id,
principle_note, transfer_note, self_checked=False)` persist reusable notes about a
principle and its transfer to a different problem. Setting `self_checked=True`
requires both notes to be non-empty; this remains a self-assessment, not automatic
validation of correctness. `topic_stats()` includes `self_checked` and
`has_reflection` for each catalog topic.

`set_classification(question_id, subject, topic_ids)` saves a manual topic choice.
Automatic classifications are suggestions, not guaranteed labels; ambiguous or
unmatched questions remain marked for review. No confidence score is calculated.
`reclassify_automatic()` refreshes suggestions against the current catalog only
for questions without a manual classification, preserving their learning
progress and notes. Complete 2009–2025 408 papers with a `408` filename use the
verified paper question-number layout only as a coarse subject hint; topic tags
remain keyword suggestions and can still need review.
`refresh_source_locations()` reparses saved original PDFs to refresh extracted
text, page spans, and visual hints while preserving question IDs, classifications,
progress, wrong-question flags, and notes.
