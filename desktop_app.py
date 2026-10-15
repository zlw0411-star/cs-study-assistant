"""Launch the optional native desktop application."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys


def _show_startup_error(message: str) -> None:
    if os.name == "nt" and sys.executable.casefold().endswith("pythonw.exe"):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "学习资料分析桌面版", 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动通用学习资料分析桌面版。")
    parser.parse_args(argv)
    if importlib.util.find_spec("PySide6") is None:
        _show_startup_error(
            "缺少桌面版依赖 PySide6。请先在项目目录运行：\n"
            "  .\\scripts\\setup.ps1 -Desktop\n"
            "然后再次启动桌面版。",
        )
        return 2

    try:
        from study_assistant.desktop import main as desktop_main

        return desktop_main([])
    except Exception:
        _show_startup_error(
            "桌面版无法启动。请运行 scripts\\start_desktop.ps1 查看依赖诊断，"
            "或在 PowerShell 中运行 .venv\\Scripts\\python.exe -X utf8 desktop_app.py。"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
