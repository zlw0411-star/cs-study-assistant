import importlib
import subprocess
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "desktop_app.py"


def run_entry(*args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(ENTRY), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )


def test_desktop_entry_help_does_not_require_qt():
    result = run_entry("--help")
    assert result.returncode == 0
    assert "桌面版" in result.stdout


def test_desktop_entry_reports_missing_qt_with_setup_command(monkeypatch):
    entry = importlib.import_module("desktop_app")
    messages = []
    monkeypatch.setattr(entry.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(entry, "_show_startup_error", messages.append)

    assert entry.main([]) == 2
    assert "PySide6" in messages[0]
    assert "setup.ps1 -Desktop" in messages[0]


def test_entry_delegates_to_desktop_main(monkeypatch):
    entry = importlib.import_module("desktop_app")
    calls = []
    fake_desktop = types.ModuleType("study_assistant.desktop")
    fake_desktop.main = lambda argv: calls.append(argv) or 0
    monkeypatch.setitem(sys.modules, "study_assistant.desktop", fake_desktop)
    monkeypatch.setattr(entry.importlib.util, "find_spec", lambda _name: object())

    assert entry.main([]) == 0
    assert calls == [[]]
