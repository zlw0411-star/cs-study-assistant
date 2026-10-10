#!/usr/bin/env python
"""Capture the exam-learning UI using only self-authored mock questions.

The MP4 is a screenshot storyboard, not a live recording. The SQLite database
is created in a temporary directory and removed when the script exits.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
OUTPUT = PROJECT / "artifacts" / "demo"
EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)
CAPTIONS = (
    "自编模拟题：先查看原卷页，再独立作答",
    "做题后重建原理，解释为什么并推导变式",
    "保存本题错因与考点原理/迁移自评",
    "错题筛选只反映本地个人记录",
    "切换异年同考点新题，关闭已有答案预览",
)


def make_cjk_pdf(text: str) -> bytes:
    """Create a searchable PDF using a standard CJK font (no external assets)."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    cidfont = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/CIDSystemInfo"): DictionaryObject(
                {
                    NameObject("/Registry"): TextStringObject("Adobe"),
                    NameObject("/Ordering"): TextStringObject("GB1"),
                    NameObject("/Supplement"): NumberObject(4),
                }
            ),
        }
    )
    cid_ref = writer._add_object(cidfont)
    type0 = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/Encoding"): NameObject("/UniGB-UCS2-H"),
            NameObject("/DescendantFonts"): ArrayObject([cid_ref]),
        }
    )
    font_ref = writer._add_object(type0)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )

    # Split into lines for comfortable page reading; input text stays entirely
    # authored in examples/mock_exams or in the short synthetic variant below.
    lines = [text[index : index + 30] for index in range(0, len(text), 30)]
    operations = ["BT /F1 15 Tf 48 780 Td"]
    for line in lines:
        encoded = (b"\xfe\xff" + line.encode("utf-16-be")).hex().upper()
        operations.append(f"<{encoded}> Tj 0 -26 Td")
    operations.append("ET")
    stream = DecodedStreamObject()
    stream.set_data(" ".join(operations).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    raw = output.getvalue()
    # Guard against an unusable demo PDF before the service imports it.
    extracted = PdfReader(BytesIO(raw)).pages[0].extract_text() or ""
    if not extracted.strip():
        raise RuntimeError("自编演示PDF文字层校验失败。")
    return raw


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_server(url: str, process: subprocess.Popen, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Streamlit演示服务提前退出。")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise TimeoutError("等待本地Streamlit演示服务超时。")


def storyboard_video(paths: list[Path], target: Path) -> None:
    import imageio.v2 as imageio

    font_path = Path(r"C:\Windows\Fonts\msyh.ttc")
    if not font_path.exists():
        font_path = Path(r"C:\Windows\Fonts\arial.ttf")
    font = ImageFont.truetype(str(font_path), 26)
    frames = []
    for index, (path, caption) in enumerate(zip(paths, CAPTIONS), 1):
        with Image.open(path) as source:
            frame = source.convert("RGB").resize((1440, 810), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(frame, "RGBA")
        draw.rectangle((0, 0, 1440, 62), fill=(16, 39, 46, 230))
        draw.text((30, 14), "分镜演示 · 根据真实页面截图制作 · 非实时录像", font=font, fill=(255, 255, 255, 255))
        draw.rectangle((0, 744, 1440, 810), fill=(16, 39, 46, 235))
        draw.text((30, 762), f"{index:02d}  {caption}", font=font, fill=(255, 255, 255, 255))
        frames.append(frame)
    imageio.mimsave(target, frames, fps=1, codec="libx264", quality=8, macro_block_size=16)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--keep-demo-data", action="store_true", help="保留临时数据库用于本地检查")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    edge = next((path for path in EDGE_CANDIDATES if path.exists()), None)
    if edge is None:
        raise RuntimeError("没有找到本机Microsoft Edge；请安装Edge或修改脚本浏览器路径。")

    sample_path = PROJECT / "examples" / "mock_exams" / "06-os-semaphore.txt"
    sample = sample_path.read_text(encoding="utf-8")
    variation = (
        "自编模拟题变式｜非官方考试资料\n操作系统\n"
        "1. 某共享缓冲区容量改为4，empty、full、mutex初值如何设置？"
        "生产者和消费者分别怎样调整P/V顺序并保持互斥？"
    )
    newest_pdf = make_cjk_pdf(sample)
    older_pdf = make_cjk_pdf(variation)
    answer_pdf = make_cjk_pdf("自编演示提示：先写出资源约束，再逐步检查P/V次序。非官方答案材料，占位示意。")
    (output / "自编模拟题06-信号量.pdf").write_bytes(newest_pdf)
    (output / "自编演示提示材料.pdf").write_bytes(answer_pdf)

    temp_context = tempfile.TemporaryDirectory(prefix="csa-exam-demo-")
    data_dir = Path(temp_context.name) / "database"
    from study_assistant import ExamService

    service = ExamService(data_dir)
    newest = service.import_pdf("2025-自编模拟题06-信号量.pdf", newest_pdf, year=2025).paper
    older = service.import_pdf("2023-自编模拟题-信号量变式.pdf", older_pdf, year=2023).paper
    for paper in (newest, older):
        question = service.list_questions(paper_id=paper.id)[0]
        service.set_classification(question.id, "操作系统", ["os_synchronization"])
    service.attach_answer_pdf(newest.id, "自编演示提示材料.pdf", answer_pdf)
    newest_question = service.list_questions(paper_id=newest.id)[0]
    older_question = service.list_questions(paper_id=older.id)[0]

    port = free_port()
    env = os.environ.copy()
    env["CSA_DATA_DIR"] = str(data_dir)
    env["PYTHONUTF8"] = "1"
    log_handle = (output / "streamlit-demo.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(PROJECT / "app.py"),
            "--server.headless=true",
            f"--server.port={port}",
            "--server.address=127.0.0.1",
            "--browser.gatherUsageStats=false",
        ],
        cwd=PROJECT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    paths = [
        output / "exam-01-original-preview.png",
        output / "exam-02-principle-transfer.png",
        output / "exam-03-study-records.png",
        output / "exam-04-wrong-filter.png",
        output / "exam-05-next-year-question.png",
    ]
    try:
        wait_server(f"http://127.0.0.1:{port}/_stcore/health", process)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(edge),
                headless=True,
                args=["--disable-gpu", "--no-first-run", "--disable-features=Translate"],
            )
            page = browser.new_page(viewport={"width": 1440, "height": 810}, device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle", timeout=45000)
            page.locator("details summary").filter(has_text="查看原卷页").click()
            page.locator("img").first.wait_for(state="visible")
            page.screenshot(path=str(paths[0]))

            page.get_by_role("button", name="做题后，开始重建原理").click()
            page.get_by_text("解释为什么", exact=True).wait_for(state="visible")
            page.screenshot(path=str(paths[1]))

            page.get_by_label("用自己的话解释原理").fill("演示填写：empty表示空槽数，full表示产品数，mutex保护共享缓冲区。")
            page.get_by_label("不同条件下怎么推导？写出你的过程或结果").fill("演示填写：容量改为4后，empty初值为4；再按P/V顺序核对生产者、消费者与互斥约束。")
            page.get_by_label("我已自行核对这两个记录").check(force=True)
            page.get_by_role("button", name="保存本考点理解与迁移记录").click()
            page.get_by_text("本考点的原理与迁移记录已保存。自检状态由你自己确认。", exact=True).wait_for(timeout=10000)
            page.get_by_label("本题复习状态（自评）").click()
            page.get_by_role("option", name="需复习").click()
            page.get_by_label("标记为错题").check(force=True)
            page.get_by_label("我的笔记").fill("演示错因：忘记检查空槽数量和互斥信号量的次序。")
            page.get_by_role("button", name="保存学习记录").click()
            page.get_by_text("本题复习记录已保存。题目状态由你自己自评。", exact=True).wait_for(timeout=10000)
            page.get_by_role("button", name="查看已有答案材料").click()
            page.screenshot(path=str(paths[2]))

            page.get_by_label("只看错题").check(force=True)
            page.get_by_text("当前筛选到 1 题", exact=True).wait_for(timeout=10000)
            page.screenshot(path=str(paths[3]))

            page.get_by_role("button", name="换年份检验迁移", exact=False).click()
            page.get_by_role("button", name="练这道变式/同类题").click()
            page.get_by_text("已切换到异年同考点新题。请先独立作答，再对照原卷与已有答案。", exact=True).wait_for(timeout=10000)
            page.get_by_text("2023 · 第 1 题", exact=False).wait_for(timeout=10000)
            if page.get_by_role("button", name="查看已有答案材料").count() != 0:
                raise AssertionError("切换新题后，前一题答案预览仍然可见。")
            page.screenshot(path=str(paths[4]))
            browser.close()
    finally:
        # Streamlit starts a server child on Windows; terminate the process tree
        # before closing/removing its redirected log file.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        log_handle.close()
        try:
            (output / "streamlit-demo.log").unlink(missing_ok=True)
        except PermissionError:
            pass
        if not args.keep_demo_data:
            temp_context.cleanup()

    storyboard_video(paths, output / "exam-storyboard.mp4")
    (output / "exam-demo-manifest.txt").write_text(
        "自编模拟题信号量演示。五张图片来自真实运行的Streamlit页面截屏。\n"
        "exam-storyboard.mp4 是静态截图分镜视频，不是实时录屏。\n"
        "题目仅来自 examples/mock_exams/06-os-semaphore.txt 和本脚本自编变式；非官方考试资料。\n"
        "答案 PDF 为自编提示占位材料，不提供标准答案。\n"
        "页面中的错因、原理和迁移记录均为演示填写，不代表真实学生记录或标准答案。\n",
        encoding="utf-8",
    )
    print("已生成五张真实页面截图和分镜MP4：", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
