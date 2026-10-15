from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from study_assistant.analysis import MaterialAnalyzer, OnlineAnalysisClient, OnlineAnalysisError, OnlineModelConfig
from study_assistant.materials import MaterialService


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CSA_DATA_DIR", PROJECT_DIR / "data")).expanduser().resolve()

st.set_page_config(page_title="资料分析助手", page_icon="📚", layout="wide")
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
</style>
""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def get_material_service(data_dir: str) -> MaterialService:
    return MaterialService(data_dir=Path(data_dir))


def delete_selected_material() -> None:
    material_id = st.session_state.get("material_selector")
    if not material_id:
        return
    service = get_material_service(str(DATA_DIR))
    service.delete_material(material_id)
    remaining = service.list_materials()
    if remaining:
        st.session_state["selected_material_id"] = remaining[0].id
        st.session_state["material_selector"] = remaining[0].id
    else:
        st.session_state.pop("selected_material_id", None)
        st.session_state.pop("material_selector", None)


service = get_material_service(str(DATA_DIR))
materials = service.list_materials()
st.markdown('<div class="hero"><div class="hero-kicker">本地资料库 · 在线按需分析</div><h1>通用资料分析助手</h1><p>导入文字资料，按需生成摘要、结构提纲、知识点和学习建议。</p></div>', unsafe_allow_html=True)
st.caption("支持可提取文字的 PDF、DOCX、TXT、MD。扫描 PDF 不做 OCR；不判分、不评价答题。")

with st.sidebar:
    st.header("资料库")
    uploaded = st.file_uploader("添加资料", type=["pdf", "docx", "txt", "md"], key="material_upload")
    if st.button("导入所选文件", key="import_material", disabled=uploaded is None, use_container_width=True):
        try:
            outcome = service.import_document(uploaded.name, uploaded.getvalue())
            st.session_state["selected_material_id"] = outcome.material.id
            st.session_state["material_selector"] = outcome.material.id
            if outcome.duplicate:
                st.info("该文件已存在，已选中原有资料。")
            else:
                st.success("资料已导入到本机。")
            for warning in outcome.warnings:
                st.warning(warning)
            st.rerun()
        except Exception as exc:
            st.error(f"导入失败：{exc}")

    if materials:
        ids = [item.id for item in materials]
        labels = {item.id: f"{item.filename} · {item.kind.upper()}" for item in materials}
        selected_default = st.session_state.get("selected_material_id")
        if selected_default not in ids:
            selected_default = ids[0]
        selected_id = st.selectbox("选择资料", ids, index=ids.index(selected_default), format_func=lambda value: labels[value], key="material_selector")
        st.session_state["selected_material_id"] = selected_id
        st.button("删除所选资料", key="delete_material", use_container_width=True, on_click=delete_selected_material)
    else:
        selected_id = None
        st.info("资料库为空。上传文字资料后即可查看和分析。")

    st.divider()
    st.header("在线模型设置")
    default_url = os.getenv("CSA_ANALYSIS_BASE_URL", "")
    default_model = os.getenv("CSA_ANALYSIS_MODEL", "")
    default_key = os.getenv("CSA_ANALYSIS_API_KEY", "")
    base_url = st.text_input("OpenAI兼容服务地址", value=default_url, placeholder="https://api.example.com/v1", key="analysis_base_url")
    model = st.text_input("模型名称", value=default_model, placeholder="填写服务商提供的模型 ID", key="analysis_model")
    api_key = st.text_input("API密钥", value=default_key, type="password", key="analysis_api_key")
    st.caption("密钥仅保存在当前会话或环境变量中，不写入本地资料库，也不会放入导出文件。")

if selected_id is None:
    st.title("开始整理你的学习资料")
    st.write("上传 PDF、DOCX、TXT 或 Markdown 文件。资料文字保存在本机，只有点击在线分析时才会发送给你配置的模型服务。")
    st.stop()

material = service.get_material(selected_id)
sources = service.get_sources(selected_id)
report = service.get_analysis(selected_id)

left, right = st.columns([1, 1.4], gap="large")
with left:
    st.subheader("资料信息")
    st.write(f"**{material.filename}**")
    st.caption(f"类型：{material.kind.upper()} · 字数：{material.character_count:,} · 来源片段：{material.source_count}")
    for warning in material.warnings:
        st.warning(warning)
    st.subheader("原文与来源")
    st.caption("报告引用 S 编号对应以下原文片段；页码或段落位置来自文件提取结果。")
    for source in sources:
        with st.expander(f"{source.id} · {source.locator}"):
            st.text(source.text)

with right:
    st.subheader("分析报告")
    st.info("在线分析会将所选资料的全部可提取文字分批发送到上方配置的模型服务；较长资料可能触发多次请求和计费。请确认服务方和资料内容适合在线处理。")
    configured = bool(base_url.strip() and model.strip() and api_key.strip())
    if not configured:
        st.caption("填写服务地址、模型和密钥后，可点击按钮开始分析；未配置时不会发送网络请求。")
    analyze_now = st.button("重新分析" if report else "生成分析报告", type="primary", key="analyze_material", disabled=not configured)
    if analyze_now:
        progress_bar = st.progress(0, text="准备分析…")
        def update_progress(done: int, total: int, message: str) -> None:
            progress_bar.progress(min(1.0, done / max(total, 1)), text=message)
        try:
            config = OnlineModelConfig(base_url=base_url, model=model, api_key=api_key)
            client = OnlineAnalysisClient(config)
            try:
                result = MaterialAnalyzer(client).analyze(material, sources, progress=update_progress)
            finally:
                client.close()
            service.save_analysis(selected_id, result)
            progress_bar.empty()
            st.success("分析完成，报告已保存到本机。")
            st.rerun()
        except Exception as exc:
            progress_bar.empty()
            message = str(exc) if isinstance(exc, (OnlineAnalysisError, ValueError)) else "在线服务发生未预期错误。"
            st.error(f"分析失败：{message} 已有成功报告仍会保留。")

    if report:
        st.markdown(f"### {report.get('title') or material.filename}")
        if report.get("material_type"):
            st.caption(f"资料类型：{report['material_type']}")
        st.markdown("#### 摘要")
        st.write(report.get("summary", ""))
        st.markdown("#### 结构提纲")
        def render_outline(nodes: list[dict], depth: int = 0) -> None:
            for node in nodes:
                indent = "　" * depth
                citations = ", ".join(node.get("source_ids", []))
                st.markdown(f"{indent}- **{node.get('title', '未命名')}**：{node.get('summary', '')}　`{citations}`")
                render_outline(node.get("children", []), depth + 1)
        render_outline(report.get("outline", []))
        st.markdown("#### 知识点")
        for point in report.get("knowledge_points", []):
            citations = ", ".join(point.get("source_ids", []))
            st.markdown(f"- **{point.get('name', '')}**：{point.get('explanation', '')}　`{citations}`")
        st.markdown("#### 学习建议")
        for suggestion in report.get("learning_suggestions", []):
            st.markdown(f"- {suggestion}")
        for warning in report.get("warnings", []):
            st.warning(warning)
        metadata = report.get("metadata", {})
        st.caption(f"模型：{metadata.get('model', '未知')} · 分析来源 {metadata.get('analyzed_source_count', '?')}/{metadata.get('total_source_count', material.source_count)} 段")
        st.warning("以上内容由 AI 生成。引用编号可展开核对原文；请自行检查摘要与建议的准确性。")
        markdown = [f"# {report.get('title') or material.filename}", "", f"资料文件：{material.filename}", "", "以上内容由 AI 生成，请结合来源原文核对。", "", f"## 摘要\n\n{report.get('summary', '')}", "", "## 结构提纲"]
        def append_outline(nodes: list[dict], depth: int = 0) -> None:
            for node in nodes:
                markdown.append(f"{'  ' * depth}- **{node.get('title', '')}**：{node.get('summary', '')}（来源：{', '.join(node.get('source_ids', []))}）")
                append_outline(node.get("children", []), depth + 1)
        append_outline(report.get("outline", []))
        markdown += ["", "## 知识点"]
        markdown.extend(f"- **{p.get('name', '')}**：{p.get('explanation', '')}（来源：{', '.join(p.get('source_ids', []))}）" for p in report.get("knowledge_points", []))
        markdown += ["", "## 学习建议"]
        markdown.extend(f"- {item}" for item in report.get("learning_suggestions", []))
        metadata = report.get("metadata", {})
        markdown += ["", "## 分析信息", f"- 模型：{metadata.get('model', '未知')}", f"- 生成时间：{metadata.get('generated_at', '未知')}", f"- 来源覆盖：{metadata.get('analyzed_source_count', '?')}/{metadata.get('total_source_count', material.source_count)} 段", "", "## 分析提示"]
        markdown.extend(f"- {warning}" for warning in report.get("warnings", []))
        markdown += ["", "## 来源索引"]
        markdown.extend(f"- **{source.id}**（{source.locator}）：{source.text}" for source in sources)
        export_payload = {"material": {"filename": material.filename, "kind": material.kind, "imported_at": material.imported_at}, "report": report, "source_index": [{"id": source.id, "locator": source.locator, "page": source.page, "paragraph": source.paragraph, "text": source.text} for source in sources]}
        st.download_button("导出 Markdown", "\n".join(markdown), file_name=f"{Path(material.filename).stem}-analysis.md", mime="text/markdown", key="download_report_md")
        st.download_button("导出 JSON", json.dumps(export_payload, ensure_ascii=False, indent=2), file_name=f"{Path(material.filename).stem}-analysis.json", mime="application/json", key="download_report_json")
    else:
        st.info("这份资料还没有分析报告。配置在线模型并点击分析后，结果会按资料分别保存。")

st.caption("资料和报告保存在本机 CSA_DATA_DIR/material.sqlite3；当前仅支持可提取文字的文件，不执行 OCR、考试评分或答案核验。")
