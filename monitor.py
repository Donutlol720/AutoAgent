"""Floating, always-on-top activity viewer for AutoAgent log files.

Run in a separate terminal: python monitor.py
It watches the most recent logs/browser_*.log file automatically.
"""

import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

LOG_DIR = Path(__file__).resolve().parent / "logs"


class AutoAgentMonitor(QWidget):
    def __init__(self):
        super().__init__()
        self.active_file = None
        self.offset = 0
        self.last_update = None
        self.finished = False
        self.latest_step = "Waiting for AutoAgent..."

        self.setWindowTitle("AutoAgent Monitor")
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowOpacity(0.96)
        self.resize(450, 330)
        self.setStyleSheet("""
            QWidget { background: #171a20; color: #e8edf2; }
            QLabel { font-size: 13px; }
            QPlainTextEdit {
                background: #232832; color: #b9e0c5;
                border: 1px solid #3a414b; border-radius: 7px;
                padding: 7px;
            }
        """)

        layout = QVBoxLayout(self)
        title = QLabel("●  AutoAgent Monitor")
        title.setStyleSheet("color: #69c8ff; font-size: 16px; font-weight: bold;")
        self.status = QLabel("Waiting for a task...")
        self.step = QLabel(self.latest_step)
        self.step.setWordWrap(True)
        self.step.setStyleSheet("color: #d4dbe5;")
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(QFont("Menlo", 10))
        self.log_box.document().setMaximumBlockCount(180)

        layout.addWidget(title)
        layout.addWidget(self.status)
        layout.addWidget(self.step)
        layout.addWidget(self.log_box, stretch=1)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 20, screen.top() + 20)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(500)
        self.poll()

    def poll(self):
        try:
            files = list(LOG_DIR.glob("browser_*.log"))
            if not files:
                self.status.setText("Waiting for logs...")
                return

            latest = max(files, key=lambda path: path.stat().st_mtime_ns)
            if latest != self.active_file:
                self.active_file = latest
                self.offset = 0
                self.last_update = None
                self.finished = False
                self.latest_step = "Connecting to the task..."
                self.log_box.clear()
                self.step.setText(self.latest_step)

            with latest.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self.offset)
                addition = handle.read()
                self.offset = handle.tell()

            if addition:
                self.last_update = time.monotonic()
                self.log_box.appendPlainText(addition.rstrip("\n"))
                scrollbar = self.log_box.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())

                for line in addition.splitlines():
                    if "AUTOAGENT_STATUS | FINISHED" in line:
                        self.finished = True
                        self.latest_step = "Task finished. Review the terminal for the result."
                    elif "AUTOAGENT_STATUS | ERROR" in line:
                        self.finished = True
                        self.latest_step = "Task encountered an error."
                    elif "AUTOAGENT_STATUS | INTERRUPTED" in line:
                        self.finished = True
                        self.latest_step = "Task interrupted."
                    elif "AUTOAGENT_STATUS | RUNNING" in line:
                        self.latest_step = "Agent started; waiting for the first action..."
                    elif "browser_use.agent.loop" in line and "step " in line:
                        # Handles both file logging and browser-use console-style messages.
                        remainder = line.split("browser_use.agent.loop", 1)[1]
                        self.latest_step = remainder.lstrip(" ]:").strip()[:150]

                self.step.setText(self.latest_step)

            if self.finished:
                self.status.setText("✓ Run complete")
            elif self.last_update is None:
                self.status.setText("Starting...")
            else:
                quiet_for = int(time.monotonic() - self.last_update)
                self.status.setText(
                    f"● Working — last log {quiet_for}s ago"
                    if quiet_for >= 2 else "● Working"
                )
        except OSError as exc:
            self.status.setText(f"Log temporarily unavailable: {exc}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AutoAgentMonitor()
    window.show()
    sys.exit(app.exec())
