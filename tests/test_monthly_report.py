from datetime import datetime

from openpyxl import load_workbook

from app.models import Order, VerificationResult
from app.services.history_service import HistoryService
from app.services.monthly_report import MonthlyReportService


def result(number: str, complete: bool, category: str = "Project"):
    item = VerificationResult(Order(2, category, "DMS", number), "Automatic", datetime(2026, 3, 12, 9))
    item.completed_at = datetime(2026, 3, 12, 9, 30)
    item.specs = item.qa = item.dcl = "Yes"
    item.drawings = item.fat = "Yes" if category == "Project" and complete else "N/A" if category == "Flow" else "Missing"
    if not complete:
        item.qa = "Missing"
    item.excel_update_status = "Saved"
    return item


def test_monthly_report_is_named_formatted_and_reconciles(tmp_path):
    history = HistoryService(tmp_path / "history.db")
    history.record(result("50000001", True))
    history.record(result("50000002", False))
    path = MonthlyReportService(history, tmp_path / "reports").generate(2026, 3)

    assert path.name == "March Monthly Excel Report.xlsx"
    workbook = load_workbook(path, data_only=False)
    sheet = workbook["Monthly Results"]
    assert sheet["A2"].value == "March 2026 DMS Order Tracker Report"
    assert sheet["B5"].value == 2
    assert sheet["E5"].value == 1
    assert sheet["H5"].value == 1
    assert sheet.freeze_panes == "A9"
    assert sheet.auto_filter.ref == "A8:K10"


def test_dashboard_reset_hides_earlier_current_month_results(tmp_path):
    history = HistoryService(tmp_path / "history.db")
    history.record(result("50000001", True))
    history.reset_current_month(datetime(2026, 3, 15, 12))
    assert history.latest(period="all", after=history.current_month_reset(datetime(2026, 3, 15))) == []
