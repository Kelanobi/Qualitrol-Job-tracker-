from openpyxl import Workbook, load_workbook
import pytest

from app.services.excel_service import ExcelService, WorkbookConflictError


def workbook_at(path, category="Project"):
    book = Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.append(["Type", "Stream", "Order"])
    sheet.append([category, "DMS", 50605782])
    book.save(path)


def test_confirmed_classification_change_updates_column_a_and_attribution(tmp_path, auth_session):
    path = tmp_path / "orders.xlsx"
    workbook_at(path)
    ExcelService(path, session_provider=lambda: auth_session).change_order_category("50605782", "Project", "Flow")
    book = load_workbook(path, data_only=False)
    assert [book.active.cell(2, column).value for column in range(1, 4)] == ["Flow", "DMS", 50605782]
    assert book.active.cell(2, 15).value is None
    assert book.active.cell(2, 16).value == auth_session.email
    book.close()


def test_stale_classification_is_not_overwritten(tmp_path, auth_session):
    path = tmp_path / "orders.xlsx"
    workbook_at(path, "Flow")
    with pytest.raises(WorkbookConflictError, match="now classified as Flow"):
        ExcelService(path, session_provider=lambda: auth_session).change_order_category("50605782", "Project", "Flow")
