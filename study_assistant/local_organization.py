"""Deterministic, rule-only navigation over one material's extracted sources.

No semantic summary is produced. Source IDs locate whole extraction segments,
not exact text spans; a boundary segment can therefore occur in two chapters.
This module neither imports the online analyzer nor opens any external resource.
"""
from __future__ import annotations

from collections.abc import Iterable
import re

from .material_types import MaterialInfo, MaterialSource

_MAX_SOURCES = 300
_MAX_CHARACTERS = 600_000
_MAX_HEADINGS = 300
_MAX_TITLE = 120
_MD = re.compile(r"^ {0,3}(#{1,9})[ \t]+(.+?)\s*$")
_CHINESE = re.compile(r"^第[零〇一二三四五六七八九十百千万两0-9]+([篇章节])(?:[ \t]+(.+))?$")
_NUMBER = re.compile(r"^([0-9]+(?:\.[0-9]+){1,8})[ \t]+(.+)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# Numbered prose and formula lines are deliberately not treated as headings.
_PROSE_OR_MATH = re.compile(r"[。！？；;!?=<>*\\^${}\[\]≤≥≠≈]|(?:\s[-−+/]\s)")


def _heading(line: str) -> tuple[int, str] | None:
    """Return textual evidence only, never a rewritten or truncated heading."""
    if len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip(" ")) >= 4:
        return None
    match = _MD.fullmatch(line)
    if match:
        title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2]).strip()
        return (len(match[1]), title) if title else None
    candidate = line.strip()
    if _PROSE_OR_MATH.search(candidate):
        return None
    match = _CHINESE.fullmatch(candidate)
    if match:
        # No unseparated suffix: “第一章的示例…” is prose, not a title.
        # Explicitly separated but obviously narrative suffixes also fall back.
        if match[2] and re.match(r"(?:的|中|将|介绍)", match[2]):
            return None
        return ({"篇": 1, "章": 2, "节": 3}[match[1]], candidate)
    match = _NUMBER.fullmatch(candidate)
    if match and re.match(r"[A-Za-z\u3400-\u9fff]", match[2]):
        # A single integer is indistinguishable from an ordinary numbered list.
        return (len(match[1].split(".")), candidate)
    return None


def _lines(sources: list[MaterialSource]):
    """Rejoin possible extraction cuts for block syntax, never for headings.

    Equal page/paragraph locators can be slices of one entry; unknown locators
    are ambiguous too. Changed pages or paragraphs delimit independent entries.
    With the extractor's fixed-size slicing, a nonfinal slice fills a source
    and cannot pack behind a different paragraph's start locator.
    Retaining the unfinished line avoids treating split fence/math tokens as
    openers or closers. No public extraction schema or source text is changed.
    """
    pending = ""
    owners: list[MaterialSource] = []
    stitched = False
    for index, source in enumerate(sources):
        lines = source.text.split("\n")
        for number, line in enumerate(lines):
            if owners:
                line = pending + line
                line_owners = owners + [source]
                uncertain = True
                pending, owners = "", []
            else:
                line_owners = [source]
                uncertain = False
            last = number == len(lines) - 1
            continuation = False
            if last and index + 1 < len(sources):
                following = sources[index + 1]
                same_page = source.page == following.page
                same_para = source.paragraph == following.paragraph
                continuation = (same_page and same_para
                                and not source.text.endswith("\n")
                                and not following.text.startswith("\n"))
            if continuation:
                pending, owners = line, line_owners
                stitched = True
            else:
                yield line, line_owners, not (uncertain or stitched)
                stitched = False


def _node(title: str, sid: str, kind: str) -> dict:
    return {
        "title": title, "summary": "", "source_ids": [sid], "children": [],
        "origin": "original" if kind == "heading" else "inferred", "kind": kind,
    }


def _validate(material: MaterialInfo, sources: Iterable[MaterialSource]) -> list[MaterialSource]:
    if not isinstance(material, MaterialInfo):
        raise TypeError("material 必须是 MaterialInfo。")
    if any(not isinstance(getattr(material, key), str) for key in
           ("id", "filename", "kind", "imported_at")):
        raise ValueError("资料标识、文件名、类型及导入时间必须为字符串。")
    if not material.filename.strip():
        raise ValueError("资料文件名不能为空。")
    if (not isinstance(material.warnings, list)
            or any(not isinstance(w, str) for w in material.warnings)):
        raise ValueError("资料警告必须为字符串列表。")
    if isinstance(sources, (str, bytes)):
        raise TypeError("sources 必须是 MaterialSource 迭代器。")
    result = []
    ids: set[str] = set()
    size = 0
    for source in sources:
        if not isinstance(source, MaterialSource):
            raise TypeError("来源必须是 MaterialSource。")
        if (not isinstance(source.id, str) or not source.id.strip()
                or source.id in ids or not isinstance(source.text, str)
                or not isinstance(source.locator, str)):
            raise ValueError("来源 ID 必须非空且唯一，正文和定位必须为字符串。")
        ids.add(source.id)
        size += len(source.text)
        result.append(source)
        if len(result) > _MAX_SOURCES or size > _MAX_CHARACTERS:
            raise ValueError("本地整理最多接受 300 个来源、600000 字符；请拆分资料。")
    return result


def build_local_report(material: MaterialInfo, sources: Iterable[MaterialSource]) -> dict:
    """Build bounded navigation, preserving input order and all supplied source IDs.

    Counts describe the supplied iterable (not a possibly stale stored count).
    At most 300 heading nodes plus one root fallback node per source are made.
    The first 301st heading permanently closes the stack; its text and all later
    sources become original-segment navigation, never part of an old chapter.
    Empty sources remain navigable. Invalid objects/duplicate IDs and inputs
    above the extraction service's size limits raise TypeError/ValueError.
    """
    source_list = _validate(material, sources)
    warnings = [w for w in material.warnings if w.strip()]
    warnings.append("仅结构提取：本地规则未调用模型，不生成摘要、知识点或学习建议；来源引用计数不代表语义完整性。")
    if material.source_count != len(source_list):
        warnings.append("资料记录的来源数量与实际输入不一致；报告计数以实际输入来源为准。")
    outline: list[dict] = []
    stack: list[tuple[int, dict]] = []
    # Root segments are coalesced by source, bounding even repeated fallbacks.
    segments: dict[str, dict] = {}
    # Sets are internal only, keeping source order in the returned lists.
    references: dict[int, set[str]] = {}
    heading_count = 0
    cutoff = False
    long_title = False
    fence_char = ""
    fence_length = 0
    math_close = ""

    def segment(source: MaterialSource) -> None:
        if source.id not in segments:
            locator = source.locator.strip()
            if not locator:
                locator = (f"第 {source.page} 页" if source.page is not None else
                           f"第 {source.paragraph} 段起" if source.paragraph is not None else "来源")
            # Reserve room for the ID so equal page locators remain distinguishable.
            label = f"原文分段 · {locator} · {source.id}"
            node = _node(label[:_MAX_TITLE], source.id, "source_segment")
            segments[source.id] = node
            outline.append(node)

    def cover(source: MaterialSource) -> None:
        if not stack:
            segment(source)
        else:
            for _, node in stack:
                seen = references[id(node)]
                if source.id not in seen:
                    seen.add(source.id)
                    node["source_ids"].append(source.id)

    empty_ids = {source.id for source in source_list if not source.text.strip()}
    # Logical lines are bounded by the validated 600k character budget.
    for line, line_sources, heading_allowed in _lines(source_list):
        source = line_sources[-1]
        for owner in line_sources:
            if owner.id in empty_ids:
                empty_ids.remove(owner.id)
                warnings.append(f"来源 {owner.id[:120]} 没有可读取的正文，保留原文分段入口。")
                segment(owner)
                cover(owner)

        def cover_line():
            for owner in line_sources:
                cover(owner)

        if cutoff:
            for owner in line_sources:
                segment(owner)
            continue
        fence = _FENCE.fullmatch(line)
        if fence_char:
            cover_line()
            if (fence and fence[1][0] == fence_char
                    and len(fence[1]) >= fence_length and not fence[2].strip()):
                fence_char = ""
            continue
        if fence:
            fence_char, fence_length = fence[1][0], len(fence[1])
            cover_line()
            continue
        stripped = line.strip()
        if math_close:
            cover_line()
            # Only standalone delimiters close display blocks. A continuation
            # suffix such as long-code-line + '$$' is not boundary evidence.
            if stripped == math_close:
                math_close = ""
            continue
        if stripped in {"$$", r"\["}:
            math_close = "$$" if stripped == "$$" else r"\]"
            cover_line()
            continue
        evidence = _heading(line) if heading_allowed else None
        if evidence:
            level, title = evidence
            level *= 10
            if _NUMBER.fullmatch(title):
                # Numeric depth is relative to the active Chinese container,
                # not an absolute chapter level; pure numeric/MD is unchanged.
                for parent_level, parent in reversed(stack):
                    if _CHINESE.fullmatch(parent["title"]):
                        level += parent_level - 10
                        break
            if len(title) > _MAX_TITLE:
                stack.clear()
                segment(source)
                long_title = True
                continue
            if heading_count == _MAX_HEADINGS:
                stack.clear()
                segment(source)
                cutoff = True
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            node = _node(title, source.id, "heading")
            references[id(node)] = {source.id}
            (stack[-1][1]["children"] if stack else outline).append(node)
            stack.append((level, node))
            heading_count += 1
            cover_line()
        elif stripped:
            cover_line()
        # Blank lines alone do not create navigation entries or extend chapters.
    if cutoff:
        warnings.append("候选标题超过 300 个：已停止标题提取并清空章节栈，超限位置及后续来源仅按原文分段导航。")
    if long_title:
        warnings.append("存在超过 120 字符的候选标题，已保守退化为原文分段，未截断或编造标题。")
    if fence_char or math_close:
        warnings.append("代码块或公式块未闭合，块内后续内容未作为标题；请核对原文。")
    if not heading_count:
        warnings.append("未识别到可靠标题；目录仅按来源页/段落提供原文分段入口，不是语义大纲。")
    if not source_list:
        warnings.append("没有可整理的来源。")
    basis = "mixed" if heading_count and segments else "headings" if heading_count else "source_segments"
    return {
        "title": material.filename[:_MAX_TITLE],
        "material_type": "其他",  # Rules do not infer a document's semantic type.
        "summary": "", "outline": outline,
        "knowledge_points": [], "learning_suggestions": [],
        "warnings": list(dict.fromkeys(warnings)),
        "metadata": {
            "mode": "local", "model": "本地规则（未调用模型）",
            "generated_at": material.imported_at,
            "analyzed_source_count": len(source_list),
            "total_source_count": len(source_list),
            "referenced_source_count": len(source_list),
            "unreferenced_source_ids": [],
            "coverage_basis": "仅结构提取：实际输入来源的导航覆盖，不代表语义分析或语义完整性；引用定位到整段来源而非精确行。",
            "heading_count": heading_count, "outline_basis": basis,
        },
    }
