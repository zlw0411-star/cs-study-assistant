from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from study_assistant import ExamService


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CSA_DATA_DIR", PROJECT_DIR / "data")).expanduser().resolve()
KNOWLEDGE_FILE = PROJECT_DIR / "examples" / "knowledge_points.json"
SUBJECTS = ["数据结构", "计算机组成原理", "操作系统", "计算机网络", "待分类"]
SUBJECT_TOPIC = "考点"
STATUS_LABELS = {"new": "未练", "review": "需复习", "mastered": "本题已理解"}
LABEL_STATUS = {v: k for k, v in STATUS_LABELS.items()}

st.set_page_config(page_title="408 真题学习台", page_icon="📘", layout="wide")
st.markdown("""
<style>
:root { --ink:#17343b; --muted:#60777b; --teal:#0e8179; --line:#dce8e5; }
html, body, [class*="css"] { font-family:'Microsoft YaHei','Noto Sans SC',sans-serif; color:var(--ink); }
.stApp { background:linear-gradient(180deg,#f3f8f6 0%,#fafbfa 240px); }
[data-testid="stHeader"] { background:transparent; }
.block-container { max-width:1250px; padding-top:1.8rem; padding-bottom:3rem; }
.hero { padding:1.25rem 1.5rem; border:1px solid var(--line); border-radius:18px; background:#fff; margin-bottom:1.1rem; }
.hero-kicker { color:var(--teal); font-size:.78rem; font-weight:700; letter-spacing:.09em; }
.hero h1 { margin:.35rem 0; font-size:1.9rem; color:var(--ink); }
.hero p { margin:0; color:var(--muted); }
h2,h3 { color:var(--ink); }
[data-testid="stVerticalBlockBorderWrapper"] { border-color:var(--line); border-radius:14px; }
.stButton>button[kind="primary"] { background:var(--teal); border-color:var(--teal); }
@media(max-width:700px) { .block-container { padding:1rem .8rem 2rem; } .hero h1 {font-size:1.5rem;} }
</style>
""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def get_exam_service(data_dir: str) -> ExamService:
    return ExamService(data_dir=Path(data_dir))


@st.cache_data(show_spinner=False)
def load_knowledge_cards(path: str, modified_at: float) -> list[dict]:
    del modified_at  # Cache invalidation key; file changes reload the cards.
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return payload.get("topics", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


exam = get_exam_service(str(DATA_DIR))
cards = load_knowledge_cards(
    str(KNOWLEDGE_FILE), KNOWLEDGE_FILE.stat().st_mtime if KNOWLEDGE_FILE.exists() else 0.0
)
card_by_id = {str(card.get("id")): card for card in cards}

if "exam_flash_notices" not in st.session_state:
    st.session_state.exam_flash_notices = []
for kind, message in st.session_state.exam_flash_notices:
    getattr(st, kind)(message)
st.session_state.exam_flash_notices = []
if "show_answer_material" not in st.session_state:
    st.session_state.show_answer_material = False


def question_label(question) -> str:
    year = str(question.year) if question.year else "年份未标"
    kind = {"choice": "选择题", "comprehensive": "综合题"}.get(question.kind, question.kind)
    topic_titles = [card_by_id[t].get("title", t) for t in question.topic_ids if t in card_by_id]
    topic_prefix = "考点" if question.classification_manual else "考点建议"
    topic_text = f" · {topic_prefix}：{', '.join(topic_titles[:2])}" if topic_titles else ""
    review_mark = " · 待核分类" if question.needs_review else ""
    return f"{year} · 第 {question.number} 题 · {kind}{topic_text}{review_mark}"


def set_flash(kind: str, message: str) -> None:
    st.session_state.exam_flash_notices.append((kind, message))


def queue_question_navigation(question_id: str, topic_id: str) -> None:
    st.session_state.pending_question_navigation = (question_id, topic_id)


pending_navigation = st.session_state.pop("pending_question_navigation", None)
if pending_navigation:
    target_id, target_topic_id = pending_navigation
    try:
        target_question = exam.get_question(target_id)
        st.session_state.subject_filter = target_question.subject
        st.session_state.topic_filter = [target_topic_id]
        st.session_state.year_filter = str(target_question.year) if target_question.year else "全部年份"
        st.session_state.status_filter = "全部状态"
        st.session_state.wrong_only = False
        st.session_state.needs_review_only = False
        st.session_state.question_search = ""
        st.session_state.selected_question = target_id
        st.session_state.active_question_id = None
        st.session_state.show_answer_material = False
        set_flash("success", "已切换到异年同考点新题。请先独立作答，再对照原卷与已有答案。")
    except Exception as exc:
        st.session_state.show_answer_material = False
        set_flash("warning", f"无法打开这道题：{exc}")


st.markdown("""
<div class="hero">
  <div class="hero-kicker">408 STUDY DESK · 真题驱动</div>
  <h1>先做真题，再补需要复习的知识点</h1>
  <p>独立作答 → 对照已有答案 → 记录错因 → 重建原理 → 推导变式 → 换年份检验迁移。掌握情况由你自己判断和记录。</p>
</div>
""", unsafe_allow_html=True)

papers = exam.list_papers()
paper_by_id = {paper.id: paper for paper in papers}

with st.sidebar:
    st.header("筛选题目")
    subject = st.selectbox("科目", SUBJECTS, index=2, key="subject_filter")
    include_recall = st.checkbox("包含回忆版（未官方核验）", value=False, key="include_recall")
    if include_recall:
        st.warning("回忆版题目可能混有解析文字或缺页，练习前请先核对原卷图像。")
    visible_papers = [p for p in papers if p.source_kind != "recall" or include_recall]
    visible_years = sorted({str(p.year) for p in visible_papers if p.year}, reverse=True)
    if st.session_state.get("year_filter", "全部年份") not in ["全部年份", *visible_years]:
        st.session_state.year_filter = "全部年份"
    selected_year_label = st.selectbox(
        "年份", ["全部年份", *visible_years], key="year_filter"
    )
    selected_status_label = st.selectbox(
        "练习状态", ["全部状态", "未练", "需复习", "本题已理解"], key="status_filter"
    )
    wrong_only = st.checkbox("只看错题", key="wrong_only")
    needs_review_only = st.checkbox("只看待复核分类", key="needs_review_only")
    query = st.text_input("搜索题干或题号", key="question_search", placeholder="输入题号或题目关键词")

    all_topics = exam.get_topics()
    subject_topic_rows = [t for t in all_topics if t.get("subject") == subject]
    topic_title_by_id = {str(t["id"]): str(t.get("title", t["id"])) for t in all_topics}
    valid_filter_topics = {str(t["id"]) for t in subject_topic_rows}
    if not set(st.session_state.get("topic_filter", [])).issubset(valid_filter_topics):
        st.session_state.topic_filter = [
            topic_id for topic_id in st.session_state.get("topic_filter", [])
            if topic_id in valid_filter_topics
        ]
    selected_topic_ids = st.multiselect(
        "考点", [str(t["id"]) for t in subject_topic_rows],
        format_func=lambda topic_id: topic_title_by_id.get(topic_id, topic_id), key="topic_filter",
    )

    st.divider()
    st.subheader("优先复习考点")
    st.caption("按当前可见原卷范围内的错题与练习记录排序；该范围由回忆版开关决定，不受年份筛选影响，也不代表考试预测。")
    try:
        active_paper_ids_for_stats = {
            p.id for p in visible_papers if p.source_kind != "recall" or include_recall
        }
        questions_for_stats = exam.list_questions(
            subject=None if subject == "待分类" else subject
        )
        counts_by_topic: dict[str, dict[str, int]] = {}
        for item in questions_for_stats:
            if item.paper_id not in active_paper_ids_for_stats:
                continue
            for topic_id in item.topic_ids:
                count = counts_by_topic.setdefault(
                    topic_id,
                    {"total": 0, "new": 0, "review": 0, "mastered": 0, "wrong": 0, "unresolved_wrong": 0},
                )
                count["total"] += 1
                count[item.status] = count.get(item.status, 0) + 1
                if item.is_wrong:
                    count["wrong"] += 1
                    if item.status != "mastered":
                        count["unresolved_wrong"] += 1
        stats = []
        for topic_id, counts in counts_by_topic.items():
            reflection = exam.get_topic_progress(topic_id)
            has_reflection = bool(
                reflection.get("principle_note", "").strip()
                or reflection.get("transfer_note", "").strip()
            )
            stats.append({
                "topic_id": topic_id,
                **counts,
                "self_checked": bool(reflection.get("self_checked", False)),
                "has_reflection": has_reflection,
            })
    except Exception:
        stats = []
    stats = sorted(
        stats,
        key=lambda row: (
            -int(row.get("unresolved_wrong", 0)),
            -int(row.get("review", 0)),
            -int(row.get("new", 0)),
            topic_title_by_id.get(str(row.get("topic_id")), ""),
        ),
    )
    if stats:
        has_practice_records = any(
            int(row.get("review", 0)) or int(row.get("wrong", 0)) or int(row.get("mastered", 0))
            for row in stats
        )
        if not has_practice_records:
            st.caption("尚无练习记录，暂按未练题量列出；做题并记录错因后再安排复习。")
        for row in stats[:8]:
            topic_id = str(row.get("topic_id", ""))
            title = topic_title_by_id.get(topic_id, topic_id or "未分类")
            wrong_count = int(row.get("unresolved_wrong", 0))
            review_count = int(row.get("review", 0))
            new_count = int(row.get("new", 0))
            st.markdown(f"**{title}**")
            has_reflection = bool(row.get("has_reflection", False))
            self_checked = bool(row.get("self_checked", False))
            reflection_label = "原理/迁移已自检" if has_reflection and self_checked else (
                "原理/迁移已记录" if has_reflection else "尚无原理/迁移记录"
            )
            st.caption(f"未解决错题 {wrong_count} · 待复习 {review_count} · 未练 {new_count} · {reflection_label}")
    else:
        st.info("完成几道题并记录错题后，这里会按你的练习情况显示优先复习考点。")

    with st.expander("导入本地真题 PDF"):
        upload_files = st.file_uploader("选择原卷 PDF", type=["pdf"], accept_multiple_files=True, key="upload_papers")
        year_text = st.text_input("年份（可留空）", key="paper_year", placeholder="例如 2025")
        source_label = st.selectbox("材料类型", ["历年原卷", "回忆版（未官方核验）"], key="source_kind")
        source_kind = "paper" if source_label == "历年原卷" else "recall"
        if upload_files and st.button("导入所选原卷", key="import_paper", type="primary"):
            if year_text and not year_text.isdigit():
                st.error("年份请填数字，或留空。")
            else:
                imported, duplicates, errors = 0, 0, []
                for uploaded in upload_files:
                    try:
                        result = exam.import_pdf(
                            uploaded.name, uploaded.getvalue(),
                            year=int(year_text) if year_text else None,
                            source_kind=source_kind,
                        )
                        duplicates += int(result.duplicate)
                        imported += int(not result.duplicate)
                        for warning in result.warnings:
                            set_flash("warning", f"{uploaded.name}：{warning}")
                    except Exception as exc:
                        errors.append(f"{uploaded.name}：{exc}")
                set_flash("success", f"已导入 {imported} 份原卷，{duplicates} 份内容重复。")
                for error in errors:
                    set_flash("error", error)
                st.rerun()

active_papers = visible_papers
active_paper_ids = {paper.id for paper in active_papers}
active_question_count = sum(int(paper.question_count or 0) for paper in active_papers)

st.caption(f"当前题库：{len(active_papers)} 份原卷 · 约 {active_question_count} 道可识别题目")

status_value = LABEL_STATUS.get(selected_status_label)
filter_year = int(selected_year_label) if selected_year_label != "全部年份" else None
try:
    if selected_topic_ids:
        merged = {}
        for topic_id in selected_topic_ids:
            for question in exam.list_questions(
                subject=subject, topic_id=topic_id, year=filter_year,
                status=status_value, wrong_only=wrong_only,
                needs_review_only=needs_review_only, query=query,
            ):
                merged[question.id] = question
        questions = list(merged.values())
    else:
        questions = exam.list_questions(
            subject=subject, year=filter_year, status=status_value,
            wrong_only=wrong_only, needs_review_only=needs_review_only, query=query,
        )
    questions = [question for question in questions if question.paper_id in active_paper_ids]
except Exception as exc:
    questions = []
    st.error(f"暂时无法读取题目：{exc}")

question_col, detail_col = st.columns([.82, 1.55], gap="large")
with question_col:
    st.subheader("题目列表")
    st.caption(f"当前筛选到 {len(questions)} 题")
    if questions:
        selected_question_id = st.selectbox(
            "选择一道题", [q.id for q in questions],
            format_func=lambda question_id: question_label(next(q for q in questions if q.id == question_id)),
            key="selected_question",
        )
        selected_question = next(q for q in questions if q.id == selected_question_id)
    else:
        selected_question = None
        st.info("没有符合条件的题目。试试清除搜索词或错题筛选、切换年份/主科目，或导入本地原卷 PDF。待复核分类仍会显示，除非你主动勾选了相应过滤条件。")

if selected_question and st.session_state.get("active_question_id") != selected_question.id:
    st.session_state.active_question_id = selected_question.id
    st.session_state.classification_subject = selected_question.subject if selected_question.subject in SUBJECTS else "待分类"
    st.session_state.classification_topics = [t for t in selected_question.topic_ids if t in card_by_id]
    st.session_state.classification_subject_previous = st.session_state.classification_subject
    st.session_state.progress_status = STATUS_LABELS.get(selected_question.status, "未练")
    st.session_state.progress_wrong = bool(selected_question.is_wrong)
    st.session_state.progress_notes = selected_question.notes or ""
    st.session_state.paper_page = max(1, int(selected_question.start_page or 1))
    st.session_state.answer_page = 1
    st.session_state.show_answer_material = False
    st.session_state.learning_started_for_question = False

with detail_col:
    if selected_question is None:
        st.subheader("开始练习")
        st.markdown("从左侧选一道题，先独立作答，再查看原卷和已有答案材料。")
    else:
        question = selected_question
        paper = paper_by_id.get(question.paper_id)
        st.subheader(question_label(question))
        classification_source = "手动修正" if question.classification_manual else "规则建议"
        st.caption(f"主科目：{question.subject} · 分类来源：{classification_source}；考点建议需结合题干自行核对。")
        if paper:
            st.caption(f"原卷：{paper.filename} · {paper.year or '年份未标'} · {paper.page_count} 页")
            if paper.source_kind == "recall":
                st.warning("回忆版·未官方核验；可能含解析且文字不完整。请以原卷页面核对题干与年份，不将其作为代表性真题练习。")
        if question.needs_review:
            st.warning("这道题的自动分类需要复核；你可以在下方手动修正科目和考点。")
        if question.matched_keywords:
            st.caption(f"匹配线索：{', '.join(question.matched_keywords)}")

        st.markdown("#### 题干文字")
        if paper and paper.source_kind == "recall" and question.text and question.text.strip():
            with st.expander("显示提取文字（回忆版可能混有解析）", expanded=False):
                st.markdown(question.text)
        elif question.text and question.text.strip():
            st.markdown(question.text)
        else:
            st.info("未提取到可显示的题干文字，请以原卷页面图片为准。")
        if question.has_visual:
            st.caption("题目包含图表、公式或版面元素，请结合原卷图片阅读。")

        if paper and paper.page_count:
            page_start = max(1, int(question.start_page or 1))
            page_end = min(int(paper.page_count), int(question.end_page or page_start))
            available_pages = list(range(page_start, page_end + 1)) or [page_start]
            st.caption(f"原卷定位：第 {page_start}–{page_end} 页" if page_end != page_start else f"原卷定位：第 {page_start} 页")
            with st.expander("查看原卷页（图表/公式以此为准）", expanded=False):
                page = st.selectbox(
                    "原卷页码", available_pages, key="paper_page",
                    format_func=lambda value: f"第 {value} 页",
                )
                try:
                    page_png = exam.render_page(paper.id, page, answer=False)
                    st.image(page_png, caption=f"原卷第 {page} 页", width="stretch")
                except Exception as exc:
                    st.warning(f"暂时无法显示原卷页：{exc}")

        if paper and paper.answer_filename:
            if st.button("查看已有答案材料", key="toggle_answer"):
                st.session_state.show_answer_material = not st.session_state.show_answer_material
            if st.session_state.show_answer_material:
                st.caption(f"答案材料：{paper.answer_filename}。请自行选择答案所在页，页码不一定和原卷对应。")
                answer_pages = list(range(1, max(1, int(paper.answer_page_count or 1)) + 1))
                answer_page = st.selectbox("答案页码", answer_pages, key="answer_page")
                try:
                    answer_png = exam.render_page(paper.id, answer_page, answer=True)
                    st.image(answer_png, caption=f"答案材料第 {answer_page} 页", width="stretch")
                except Exception as exc:
                    st.warning(f"暂时无法显示答案材料：{exc}")
        elif paper:
            st.info("当前原卷没有关联答案 PDF。你可以先独立作答并记录学习情况。")

        st.divider()
        st.markdown("#### 对照知识点，记录自己的判断")
        classification_subject = st.selectbox(
            "手动调整主科目", SUBJECTS, key="classification_subject"
        )
        classification_topics = [
            t for t in exam.get_topics() if t.get("subject") == classification_subject
        ]
        classification_topic_ids = [str(t["id"]) for t in classification_topics]
        if st.session_state.get("classification_subject_previous") != classification_subject:
            st.session_state.classification_topics = [
                topic_id for topic_id in st.session_state.get("classification_topics", [])
                if topic_id in classification_topic_ids
            ]
            st.session_state.classification_subject_previous = classification_subject
        classification_topic_labels = {str(t["id"]): str(t.get("title", t["id"])) for t in classification_topics}
        chosen_topic_ids = st.multiselect(
            "手动调整考点分类", classification_topic_ids,
            format_func=lambda topic_id: classification_topic_labels.get(topic_id, topic_id),
            key="classification_topics",
        )
        if st.button("保存分类修正", key="save_classification"):
            try:
                exam.set_classification(question.id, classification_subject, chosen_topic_ids)
                set_flash("success", "分类已保存。")
                st.rerun()
            except Exception as exc:
                st.error(f"保存分类失败：{exc}")

        selected_card_ids = chosen_topic_ids or [t for t in question.topic_ids if t in card_by_id]
        if selected_card_ids:
            st.markdown("#### 补知识原理，再检验迁移")
            if not st.session_state.get("learning_started_for_question", False):
                st.info("先独立完成本题并对照原卷/已有答案；准备好后再打开知识卡，重建原理并推导变式。")
                if st.button("做题后，开始重建原理", key="start_topic_learning"):
                    st.session_state.learning_started_for_question = True
                    st.rerun()
            else:
                for topic_id in selected_card_ids:
                  card = card_by_id.get(topic_id)
                  if not card:
                      continue
                  with st.expander(f"补习知识卡：{card.get('title', topic_id)}", expanded=True):
                      st.markdown(f"**{card.get('title', topic_id)}**")
                      st.write(card.get("summary", ""))
                      if card.get("prerequisites"):
                          prerequisite_titles = [
                              card_by_id.get(str(prerequisite), {}).get("title", str(prerequisite))
                              for prerequisite in card["prerequisites"]
                          ]
                          st.caption("前置知识：" + "、".join(prerequisite_titles))
                      checklist = card.get("checklist", [])
                      if checklist:
                          st.markdown("**理解后确认自己能做到：**")
                          for item in checklist:
                              st.markdown(f"- {item}")
                      mistakes = card.get("common_mistakes", [])
                      if mistakes:
                          st.markdown("**常见错误**")
                          for mistake in mistakes:
                              st.markdown(f"- {mistake}")

                      why_questions = card.get("why_questions", [])
                      if why_questions:
                          st.markdown("**解释为什么**")
                          for why_question in why_questions:
                              st.markdown(f"- {why_question}")
                      transfer_prompt = card.get("transfer_prompt", "")
                      if transfer_prompt:
                          st.markdown("**变式问题**")
                          st.write(transfer_prompt)
                      if card.get("transfer_hint"):
                          with st.expander("展开变式思路提示", expanded=False):
                              st.write(card["transfer_hint"])

                      try:
                          topic_progress = exam.get_topic_progress(topic_id) or {}
                      except Exception:
                          topic_progress = {}
                      principle_key = f"topic_principle_{topic_id}"
                      transfer_key = f"topic_transfer_{topic_id}"
                      checked_key = f"topic_self_checked_{topic_id}"
                      if principle_key not in st.session_state:
                          st.session_state[principle_key] = topic_progress.get("principle_note", "")
                      if transfer_key not in st.session_state:
                          st.session_state[transfer_key] = topic_progress.get("transfer_note", "")
                      if checked_key not in st.session_state:
                          st.session_state[checked_key] = bool(topic_progress.get("self_checked", False))
                      principle_note = st.text_area(
                          "用自己的话解释原理", key=principle_key, height=100,
                          placeholder="说清为什么成立、依赖什么条件，以及它和相近概念的区别。",
                      )
                      transfer_note = st.text_area(
                          "不同条件下怎么推导？写出你的过程或结果", key=transfer_key, height=100,
                          placeholder="改变一个题设条件，写出推理步骤和结论。",
                      )
                      self_checked = st.checkbox(
                          "我已自行核对这两个记录", key=checked_key,
                      )
                      if st.button(
                          "保存本考点理解与迁移记录", key=f"save_topic_progress_{topic_id}"
                      ):
                          try:
                              exam.save_topic_progress(
                                  topic_id, principle_note, transfer_note, self_checked=self_checked
                              )
                              set_flash("success", "本考点的原理与迁移记录已保存。自检状态由你自己确认。")
                              st.rerun()
                          except Exception as exc:
                              st.error(f"无法保存：{exc}")

                  try:
                      related = exam.list_questions(subject=classification_subject, topic_id=topic_id)
                  except Exception:
                      related = []
                  related = [item for item in related if item.id != question.id and item.paper_id in active_paper_ids]
                  related = [item for item in related if item.year != question.year and item.status == "new"][:4]
                  if related:
                      with st.expander(f"换年份检验迁移 · {classification_topic_labels.get(topic_id, topic_id)}"):
                          for item in related:
                              st.markdown(question_label(item))
                              st.button(
                                  "练这道变式/同类题",
                                  key=f"practice_related_{topic_id}_{item.id}",
                                  on_click=queue_question_navigation,
                                  args=(item.id, topic_id),
                              )
                          st.caption("选择异年未练题后，会自动切换年份、科目和考点，并关闭答案预览。先独立推导，再对照原卷和已有答案。")
                  else:
                      st.caption("当前没有其他年份未练的同考点题；可在年份筛选中换一年查看。")
        else:
            st.info("暂时没有匹配的知识卡。你可以修正考点分类，或先记录这道题的薄弱处。")

        status_label = st.selectbox("本题复习状态（自评）", list(LABEL_STATUS.keys()), key="progress_status")
        is_wrong = st.checkbox("标记为错题", key="progress_wrong")
        notes = st.text_area("我的笔记", key="progress_notes", height=100, placeholder="记录概念混淆、条件漏看、推导错误等错因，或下次复习提醒")
        if st.button("保存学习记录", key="save_progress", type="primary"):
            try:
                exam.save_progress(
                    question.id,
                    status=LABEL_STATUS[status_label],
                    is_wrong=is_wrong,
                    notes=notes,
                )
                set_flash("success", "本题复习记录已保存。题目状态由你自己自评。")
                st.rerun()
            except Exception as exc:
                st.error(f"保存学习记录失败：{exc}")

st.divider()
with st.expander("关联答案 PDF 到原卷"):
    if papers:
        selected_paper_id = st.selectbox(
            "选择要关联答案的原卷", [p.id for p in papers],
            format_func=lambda paper_id: f"{paper_by_id[paper_id].year or '年份未标'} · {paper_by_id[paper_id].filename}",
            key="paper_for_answer",
        )
        answer_upload = st.file_uploader("选择答案 PDF", type=["pdf"], key="answer_pdf")
        if st.button("关联答案材料", key="attach_answer", disabled=answer_upload is None):
            try:
                warnings = exam.attach_answer_pdf(selected_paper_id, answer_upload.name, answer_upload.getvalue())
                set_flash("success", f"答案材料已关联：{answer_upload.name}")
                for warning in warnings:
                    set_flash("warning", warning)
                st.rerun()
            except Exception as exc:
                st.error(f"关联答案失败：{exc}")
    else:
        st.info("先导入一份原卷，再关联答案 PDF。")

with st.expander("管理本机原卷"):
    if papers:
        paper_to_delete = st.selectbox(
            "选择要删除的原卷及其学习记录",
            [p.id for p in papers],
            format_func=lambda paper_id: f"{paper_by_id[paper_id].year or '年份未标'} · {paper_by_id[paper_id].filename}",
            key="paper_to_delete",
        )
        confirm_delete = st.checkbox(
            "我确认从本机删除这份原卷、关联答案和题目记录",
            key="confirm_delete_paper",
        )
        if st.button("删除所选原卷", key="delete_paper", disabled=not confirm_delete):
            try:
                deleted = exam.delete_paper(paper_to_delete)
                if deleted:
                    st.session_state.pop("active_question_id", None)
                    st.session_state.pop("selected_question", None)
                    st.session_state.pop("show_answer_material", None)
                    set_flash("success", "原卷、关联答案和对应题目记录已从本机删除。")
                else:
                    set_flash("warning", "这份原卷已不存在，页面已刷新。")
                st.rerun()
            except Exception as exc:
                st.error(f"删除原卷失败：{exc}")
    else:
        st.info("题库为空。导入原卷后可在这里管理本机文件。")

with st.expander("维护自动分类建议"):
    st.caption("知识点目录更新后，可刷新由系统建议的题目分类。手动修正、复习记录和知识点原理/迁移记录会保留。")
    if st.button("刷新自动建议分类", key="reclassify_automatic"):
        try:
            refreshed = exam.reclassify_automatic()
            st.session_state.active_question_id = None
            st.session_state.show_answer_material = False
            set_flash("success", f"已刷新 {refreshed} 道题的自动分类建议；手动修正和学习记录已保留。")
            st.rerun()
        except Exception as exc:
            st.error(f"刷新分类建议失败：{exc}")

st.caption("主科目由题号规则建议，知识点由关键词辅助建议；均可手动修正，不代表自动分类准确率。复习状态和原理自检由你记录；页面不自动判卷或生成真题答案。")
