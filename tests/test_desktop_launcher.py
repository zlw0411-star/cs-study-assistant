from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "启动桌面版.cmd"


@pytest.mark.skipif(sys.platform != "win32", reason="The Windows CMD launcher only runs on Windows")
def test_cmd_launcher_runs_powerShell_from_unicode_space_path_and_returns_exit_code(tmp_path):
    cmd = shutil.which("cmd.exe")
    system_root = os.environ.get("SystemRoot")
    assert cmd and system_root
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    assert powershell.is_file()

    content = LAUNCHER.read_bytes()
    assert content and all(line.endswith(b"\r") for line in content.split(b"\n")[:-1])
    assert all(byte < 128 for byte in content)  # Keep cmd.exe's OEM parser out of the encoding path.

    project = tmp_path / "桌面 launcher 含空格"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    launcher = project / "启动桌面版.cmd"
    shutil.copyfile(LAUNCHER, launcher)
    marker = project / "launcher-invoked.txt"
    stub = scripts / "start_desktop.ps1"

    def invoke_with_exit_code(code: int) -> subprocess.CompletedProcess[str]:
        stub.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            "$ProjectRoot = Split-Path -Parent $PSScriptRoot\n"
            "[IO.File]::WriteAllText((Join-Path $ProjectRoot 'launcher-invoked.txt'), $ProjectRoot, [Text.Encoding]::UTF8)\n"
            f"exit {code}\n",
            encoding="ascii",
            newline="\r\n",
        )
        command = f'"{cmd}" /d /s /c ""{launcher}" <NUL"'
        return subprocess.run(
            command,
            cwd=project,
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=15,
            check=False,
        )

    success = invoke_with_exit_code(0)
    assert success.returncode == 0, (success.stdout, success.stderr)
    assert marker.read_text(encoding="utf-8-sig") == str(project)
    assert "not recognized" not in (success.stdout + success.stderr).casefold()

    marker.unlink()
    failure = invoke_with_exit_code(7)
    assert failure.returncode == 7, (failure.stdout, failure.stderr)
    assert marker.read_text(encoding="utf-8-sig") == str(project)
    assert "not recognized" not in (failure.stdout + failure.stderr).casefold()
