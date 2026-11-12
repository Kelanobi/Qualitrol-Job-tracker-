from __future__ import annotations

from pathlib import Path
from openpyxl import load_workbook

from app.phase2.models import ProjectOrder
from app.services.excel_service import WorkbookError, normalize_identifier


class FlowEligibilityService:
    def __init__(self, workbook_path: Path, worksheet_name: str = "Sheet1"):
        self.workbook_path = Path(workbook_path)
        self.worksheet_name = worksheet_name

    def read_eligible_flows(self) -> list[ProjectOrder]:
        if not self.workbook_path.exists():
            raise WorkbookError(f"The workbook could not be found at {self.workbook_path}.")
        workbook = load_workbook(self.workbook_path, read_only=True, data_only=True)
        try:
            if self.worksheet_name not in workbook.sheetnames:
                raise WorkbookError(f"Worksheet '{self.worksheet_name}' was not found.")
            sheet = workbook[self.worksheet_name]
            headers = {}
            for column in range(1, sheet.max_column + 1):
                value = str(sheet.cell(1, column).value or "").strip().casefold()
                if value:
                    headers.setdefault(value, []).append(column)
            term_columns = headers.get("term", [])
            if len(term_columns) != 1:
                raise WorkbookError("The Phase 2 Term header is missing or ambiguous.")
            customer_columns = headers.get("customer", [])
            results = []
            for row in range(2, sheet.max_row + 1):
                if str(sheet.cell(row, 1).value or "").strip().casefold() != "flow":
                    continue
                if str(sheet.cell(row, 2).value or "").strip().casefold() != "dms":
                    continue
                so = normalize_identifier(sheet.cell(row, 3).value)
                if not so:
                    raise WorkbookError(f"DMS Flow row {row} has an empty SO in column C.")
                results.append(ProjectOrder(
                    row=row, so=so,
                    term=str(sheet.cell(row, term_columns[0]).value or "").strip() or None,
                    workbook_customer=(str(sheet.cell(row, customer_columns[0]).value or "").strip() or None)
                    if len(customer_columns) == 1 else None,
                    category="Flow", value_stream="DMS",
                ))
            return results
        finally:
            workbook.close()

    def find_flow(self, so: str) -> ProjectOrder:
        normalized = normalize_identifier(so)
        orders = {item.so: item for item in self.read_eligible_flows()}
        if normalized not in orders:
            raise WorkbookError(f"Order {normalized} is not an eligible DMS Flow order.")
        return orders[normalized]
