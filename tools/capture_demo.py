#!/usr/bin/env python
"""Capture real browser screenshots of the running local Streamlit demo."""
import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wait_server(port, process, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Streamlit exited with code {process.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise TimeoutError("Streamlit server did not become ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "demo")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--browser", choices=("msedge", "chromium"), default="msedge")
    parser.add_argument("--method", choices=("keyword", "dense", "hybrid"), default="keyword")
    parser.add_argument("--generate", action="store_true", help="exercise the configured local generator")
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Install Playwright in the project environment, then run this tool again.") from exc

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="csa-demo-") as data_dir:
        env = os.environ.copy()
        env["CSA_DATA_DIR"] = data_dir
        env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
        command = [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
                   "--server.headless=true", f"--server.port={args.port}", "--browser.gatherUsageStats=false"]
        server = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_server(args.port, server)
            with sync_playwright() as playwright:
                if args.browser == "msedge":
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                else:
                    browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
                page.goto(f"http://127.0.0.1:{args.port}", wait_until="networkidle")
                page.screenshot(path=str(args.output_dir / "01-empty-library.png"), full_page=True)
                page.get_by_role("button", name="导入示例笔记").click()
                page.wait_for_timeout(500)
                page.screenshot(path=str(args.output_dir / "02-example-imported.png"), full_page=True)
                if args.method in {"dense", "hybrid"}:
                    page.get_by_role("button", name="建立 / 更新索引").click()
                    page.get_by_text("索引已更新").wait_for(timeout=180000)
                    page.screenshot(path=str(args.output_dir / "03-index-ready.png"), full_page=True)
                page.get_by_label("问题").fill("进程和线程有什么区别？")
                label = {"keyword": "关键词检索", "dense": "语义检索", "hybrid": "混合检索"}[args.method]
                method = page.get_by_role("combobox", name="检索方式")
                for _ in range({"keyword": 0, "dense": 1, "hybrid": 2}[args.method]):
                    method.click()
                    method.press("ArrowDown")
                    method.press("Enter")
                assert method.input_value() == label
                checkbox = page.get_by_role("checkbox", name="根据找到的资料生成回答")
                if checkbox.is_checked() and not args.generate:
                    checkbox.uncheck(force=True)
                elif not checkbox.is_checked() and args.generate:
                    checkbox.check(force=True)
                page.get_by_role("button", name="检索并回答" if args.generate else "检索资料").click()
                page.get_by_text("资料出处").wait_for(timeout=10000)
                result_name = "04-generated-answer.png" if args.generate else ("04-hybrid-results.png" if args.method != "keyword" else "03-keyword-results.png")
                page.screenshot(path=str(args.output_dir / result_name), full_page=True)
                browser.close()
            storyboard_steps = ["01-empty-library.png", "02-example-imported.png"]
            if args.method in {"dense", "hybrid"}:
                storyboard_steps.append("03-index-ready.png")
            storyboard_steps.append(result_name)
            descriptions = [
                "These are real headless-browser captures of the local Streamlit page, not a live screen recording.",
                "1. `01-empty-library.png` — launch with an empty temporary database.",
                "2. `02-example-imported.png` — import a self-authored OS note.",
            ]
            if args.method in {"dense", "hybrid"}:
                descriptions.append("3. `03-index-ready.png` — build the configured semantic index.")
            descriptions.append(f"{len(storyboard_steps)}. `{result_name}` — run {label} retrieval" + (" and local generation." if args.generate else " and inspect retrieved sources."))
            (args.output_dir / "storyboard.md").write_text("# Screenshot storyboard\n\n" + "\n\n".join(descriptions) + "\n", encoding="utf-8")
            print(f"Captured three real page screenshots in {args.output_dir}")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()
