"""One-click launcher for AutoAgent, desktop monitor, and remote dashboard.

In TRAE, open this file and choose Run Python File in Terminal.
The remote dashboard is visible on devices on the same trusted Wi-Fi.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "bin" / "python"


def stop(process: subprocess.Popen):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main() -> None:
    if not PYTHON.is_file():
        raise SystemExit(f"Missing virtual environment at {PYTHON}")
    for name in ("monitor.py", "browser_agent.py", "remote_monitor.py"):
        if not (ROOT / name).is_file():
            raise SystemExit(f"Missing {name} in {ROOT}")

    print("Opening desktop monitor...", flush=True)
    monitor = subprocess.Popen([str(PYTHON), str(ROOT / "monitor.py")], cwd=ROOT)

    print("Starting read-only remote dashboard...", flush=True)
    remote = subprocess.Popen(
        [str(PYTHON), str(ROOT / "remote_monitor.py"), "--lan"], cwd=ROOT
    )

    try:
        print("Starting browser agent...", flush=True)
        exit_code = subprocess.call(
            [str(PYTHON), str(ROOT / "browser_agent.py")], cwd=ROOT
        )
        if exit_code:
            print(f"\nBrowser agent exited with code {exit_code}.")
        print("\nThe monitors remain open for reviewing logs.")
        input("Press Enter to close both monitors and exit... ")
    except (KeyboardInterrupt, EOFError):
        print("\nLauncher interrupted.")
    finally:
        stop(remote)
        stop(monitor)


if __name__ == "__main__":
    main()
