#!/usr/bin/env python
"""Turn the three real browser screenshots into a clearly labeled storyboard MP4."""
import argparse
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BASE_STEPS = [
    ("01-empty-library.png", "1 / 3  空资料库", "启动本地学习助手，资料仅保存在临时演示库中"),
    ("02-example-imported.png", "2 / 3  导入自编示例", "导入一份自编操作系统笔记"),
    ("03-keyword-results.png", "3 / 3  关键词检索", "搜索进程与线程，并检查返回的原文出处"),
]


def fit_image(path, box):
    image = Image.open(path).convert("RGB")
    image.thumbnail(box, Image.Resampling.LANCZOS)
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "artifacts" / "demo")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "demo" / "storyboard.mp4")
    parser.add_argument("--seconds-per-step", type=float, default=2.5)
    parser.add_argument("--fps", type=int, default=12)
    args = parser.parse_args()
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    title_font = ImageFont.truetype(str(font_path), 28) if font_path.exists() else ImageFont.load_default()
    caption_font = ImageFont.truetype(str(font_path), 20) if font_path.exists() else ImageFont.load_default()
    if (args.input_dir / "04-generated-answer.png").exists():
        steps = [
            ("01-empty-library.png", "1 / 4  空资料库", "启动本地学习助手，资料仅保存在临时演示库中"),
            ("02-example-imported.png", "2 / 4  导入自编示例", "导入一份自编操作系统笔记"),
            ("03-index-ready.png", "3 / 4  建立索引", "为真实语义检索准备本地索引"),
            ("04-generated-answer.png", "4 / 4  检索与回答", "查看模型回答、引用和原文出处"),
        ]
    elif (args.input_dir / "04-hybrid-results.png").exists():
        steps = [
            ("01-empty-library.png", "1 / 4  空资料库", "启动本地学习助手，资料仅保存在临时演示库中"),
            ("02-example-imported.png", "2 / 4  导入自编示例", "导入一份自编操作系统笔记"),
            ("03-index-ready.png", "3 / 4  建立索引", "为语义检索准备本地索引"),
            ("04-hybrid-results.png", "4 / 4  混合检索", "检查返回的原文出处"),
        ]
    else:
        steps = BASE_STEPS
    frames = []
    total = len(steps)
    for filename, title, caption in steps:
        image_path = args.input_dir / filename
        if not image_path.exists():
            raise SystemExit(f"Missing real screenshot: {image_path}; run tools/capture_demo.py first")
        canvas = Image.new("RGB", (1280, 720), "#f5faf8")
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 1280, 82), fill="#17343b")
        draw.text((38, 12), title, font=title_font, fill="white")
        draw.text((38, 48), caption, font=caption_font, fill="#d8ece8")
        screen = fit_image(image_path, (1220, 620))
        canvas.paste(screen, ((1280 - screen.width) // 2, 90 + (620 - screen.height) // 2))
        frames.extend([np.asarray(canvas)] * max(1, int(args.seconds_per_step * args.fps)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    imageio.mimwrite(args.output, frames, fps=args.fps, codec="libx264", quality=8, macro_block_size=1)
    (args.output.parent / "video-notes.md").write_text(
        "# 演示视频说明\n\n"
        f"该 MP4 由真实本地 Streamlit 页面截图加字幕制成，共{total}个静态分镜，不是连续屏幕录制。原始截图保留在同一目录。\n",
        encoding="utf-8")
    print(f"Wrote storyboard video: {args.output}")


if __name__ == "__main__":
    main()
