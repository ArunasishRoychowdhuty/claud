import subprocess
import sys
from pathlib import Path

from core.python_runtime import ensure_windows_runtime_env, launch_hint

ROOT = Path(__file__).resolve().parent
ensure_windows_runtime_env()


def main() -> None:
    print("Installing MARK XXX in editable mode...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-e", "."], check=True, cwd=str(ROOT))

    print("Installing Playwright browsers...")
    subprocess.run([sys.executable, "-m", "playwright", "install"], check=True, cwd=str(ROOT))

    print(f"\nSetup complete! Run 'mark-xxx' or '{launch_hint()}' to start MARK XXX.")


if __name__ == "__main__":
    main()
