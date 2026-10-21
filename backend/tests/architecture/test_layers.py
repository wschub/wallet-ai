import shutil
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _lint_imports_cmd() -> list[str]:
    exe = shutil.which("lint-imports") or str(Path(sys.executable).parent / "lint-imports")
    return [exe]


def test_architecture_layers():
    result = subprocess.run(
        _lint_imports_cmd(),
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr