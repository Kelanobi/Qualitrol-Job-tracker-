from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.controller import ApplicationController
from app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet((ROOT / "app" / "ui" / "styles.qss").read_text(encoding="utf-8"))
    database = Path(tempfile.mkdtemp(prefix="dms-tracker-capture-")) / "history.db"
    controller = ApplicationController(
        Path(r"C:\Users\kelvin.obi\Downloads\123.xlsx"), ROOT / ".unavailable-document-root", database
    )
    window = MainWindow(controller, ROOT / "assets")
    window.resize(1536, 1024)
    window.show()

    def capture():
        window.grab().save(str(ROOT / "implementation-dashboard.png"))
        app.quit()

    QTimer.singleShot(800, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

