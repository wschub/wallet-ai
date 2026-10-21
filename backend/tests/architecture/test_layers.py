import subprocess


def test_architecture_layers():
    result = subprocess.run(
        ["uv", "run", "lint-imports"],
        cwd="/Users/juandavidrodriguez/Documents/labs23/PORTFOLIO-26/wallet-ai/backend",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"lint-imports failed:\n{result.stdout}\n{result.stderr}"
