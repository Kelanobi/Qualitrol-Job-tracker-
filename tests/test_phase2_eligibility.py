from pathlib import Path

import pytest
from openpyxl import Workbook

from app.phase2.project_eligibility import ProjectEligibilityService
from app.services.excel_service import WorkbookError


def make_workbook(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_only_dms_project_rows_are_eligible_and_term_is_dynamic(tmp_path):
    path = tmp_path / "orders.xlsx"
    make_workbook(path, ["Class", "Stream", "SO", "Unused", "Term", "Customer"], [
        ["Project", "DMS", 50577444, None, "CIP", "Commercial Co"],
        ["Flow", "DMS", 50577445, None, "EXW", "Flow Co"],
        ["Project", "IP", 50577446, None, "FCA", "IP Co"],
    ])

    orders = ProjectEligibilityService(path).read_eligible_projects()

    assert [(order.so, order.term, order.workbook_customer) for order in orders] == [
        ("50577444", "CIP", "Commercial Co")
    ]


@pytest.mark.parametrize("headers", [
    ["Class", "Stream", "SO"],
    ["Class", "Stream", "SO", "Term", "TERM"],
])
def test_missing_or_ambiguous_term_is_blocked(tmp_path, headers):
    path = tmp_path / "orders.xlsx"
    make_workbook(path, headers, [["Project", "DMS", "50577444", "CIP", "EXW"]])

    with pytest.raises(WorkbookError, match="Term header"):
        ProjectEligibilityService(path).read_eligible_projects()


def test_empty_project_so_is_not_silently_accepted(tmp_path):
    path = tmp_path / "orders.xlsx"
    make_workbook(path, ["Class", "Stream", "SO", "Term"], [["Project", "DMS", None, "CIP"]])

    with pytest.raises(WorkbookError, match="empty SO"):
        ProjectEligibilityService(path).read_eligible_projects()
