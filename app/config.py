from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "DMS Order Tracker"
REPORT_DIR = Path(r"C:\DMS Order Tracker")
WORKBOOK_PATH = Path(r"T:\Temp\DMS\123.xlsx")
WORKSHEET_NAME = "Sheet1"
DOCUMENT_ROOT = Path(r"S:\Manufacturing\Public\2 Jobfiles Released")
SMARTSHEET_SHEET_ID = os.getenv("DMS_SMARTSHEET_SHEET_ID", "w9PHJHJXQCppp3xfXWQcGxhqXP43MJXcfJFp3q41")


def _application_password_hash() -> str:
    configured = os.getenv("DMS_APPLICATION_PASSWORD_HASH", "").strip()
    if configured:
        return configured
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "_secure" / "application_password.hash"
    try:
        return bundled.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


APPLICATION_PASSWORD_HASH = _application_password_hash()
# Email prefix -> Windows username, used only for explicitly approved exceptions.
APPROVED_USERNAME_MAPPINGS: dict[str, str] = {}


def user_data_dir() -> Path:
    root = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / "DMSOrderTracker"
    path.mkdir(parents=True, exist_ok=True)
    return path


def history_database_path() -> Path:
    return user_data_dir() / "history.db"


def log_path() -> Path:
    return user_data_dir() / "dms_order_tracker.log"

