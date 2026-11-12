from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from app.config import DOCUMENT_ROOT, WORKBOOK_PATH, history_database_path, log_path
from app.controller import ApplicationController
from app.services.history_service import HistoryService
from app.services.monthly_report import MonthlyReportService
from app.services.monthly_scheduler import install_monthly_task
from app.services.excel_service import ExcelService
from app.ui.main_window import MainWindow


def resource_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets"


def main() -> int:
    logging.basicConfig(
        filename=log_path(), level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if "--monthly-report" in sys.argv:
        try:
            MonthlyReportService(
                HistoryService(history_database_path()), orders_provider=ExcelService(WORKBOOK_PATH).read_orders,
            ).generate_due()
            return 0
        except Exception:
            logging.getLogger("monthly_report").exception("Silent monthly report generation failed")
            return 1
    install_monthly_task()
    try:
        MonthlyReportService(
            HistoryService(history_database_path()), orders_provider=ExcelService(WORKBOOK_PATH).read_orders,
        ).generate_due()
    except Exception:
        logging.getLogger("monthly_report").exception("Silent monthly report catch-up failed")

    def report_unhandled(exc_type, exc_value, exc_traceback):
        logging.getLogger("unhandled").critical(
            "Unhandled application exception", exc_info=(exc_type, exc_value, exc_traceback)
        )
        try:
            QMessageBox.critical(
                None, "DMS Order Tracker error",
                f"An unexpected error occurred. The application will remain open.\n\n{exc_value}\n\n"
                f"Details were written to {log_path()}.",
            )
        except Exception:
            pass
    sys.excepthook = report_unhandled
    app = QApplication(sys.argv)
    app.setApplicationName("DMS Order Tracker")
    style_path = Path(__file__).resolve().parent / "app" / "ui" / "styles.qss"
    app.setStyleSheet(style_path.read_text(encoding="utf-8"))
    controller = ApplicationController(WORKBOOK_PATH, DOCUMENT_ROOT, history_database_path())
    window = MainWindow(controller, resource_dir())
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

