from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from app.phase2.models import ProjectOrder
from app.services.excel_service import WorkbookError, normalize_identifier


class ProjectEligibilityService:
    def __init__(self, workbook_path: Path, worksheet_name: str = "Sheet1"):
        self.workbook_path = Path(workbook_path)
        self.worksheet_name = worksheet_name

    @staticmethod
    def _headers(sheet) -> dict[str, list[int]]:
        headers: dict[str, list[int]] = {}
        for column in range(1, sheet.max_column + 1):
            value = str(sheet.cell(1, column).value or "").strip().casefold()
            if value:
                headers.setdefault(value, []).append(column)
        return headers

    def read_eligible_orders(self) -> list[ProjectOrder]:
        if not self.workbook_path.exists():
            raise WorkbookError(f"The workbook could not be found at {self.workbook_path}.")
        try:
            workbook = load_workbook(self.workbook_path, read_only=True, data_only=True)
            if self.worksheet_name not in workbook.sheetnames:
                workbook.close()
                raise WorkbookError(f"Worksheet '{self.worksheet_name}' was not found.")
            sheet = workbook[self.worksheet_name]
            headers = self._headers(sheet)
            term_columns = headers.get("term", [])
            if len(term_columns) != 1:
                workbook.close()
                reason = "missing" if not term_columns else "ambiguous"
                raise WorkbookError(f"The Phase 2 Term header is {reason}; Incoterms cannot be resolved safely.")
            customer_columns = headers.get("customer", [])
            if len(customer_columns) > 1:
                workbook.close()
                raise WorkbookError("The Customer header is ambiguous; commercial customer evidence cannot be matched safely.")
            term_column = term_columns[0]
            customer_column = customer_columns[0] if customer_columns else None
            orders: list[ProjectOrder] = []
            for row in range(2, sheet.max_row + 1):
                category = str(sheet.cell(row, 1).value or "").strip()
                stream = str(sheet.cell(row, 2).value or "").strip()
                if category.casefold() not in {"project", "flow"} or stream.casefold() != "dms":
                    continue
                so = normalize_identifier(sheet.cell(row, 3).value)
                if not so:
                    workbook.close()
                    raise WorkbookError(f"DMS Project row {row} has an empty SO in column C.")
                term = str(sheet.cell(row, term_column).value or "").strip() or None
                customer = (
                    str(sheet.cell(row, customer_column).value or "").strip() or None
                    if customer_column else None
                )
                orders.append(ProjectOrder(
                    row=row, so=so, term=term, workbook_customer=customer,
                    category=category.title(), value_stream="DMS",
                ))
            workbook.close()
            return orders
        except WorkbookError:
            raise
        except (OSError, PermissionError) as exc:
            raise WorkbookError(f"The workbook could not be read for Phase 2: {exc}") from exc

    def read_eligible_projects(self) -> list[ProjectOrder]:
        return [order for order in self.read_eligible_orders() if order.category == "Project"]

    def find_project(self, so: str) -> ProjectOrder:
        normalized = normalize_identifier(so)
        projects = {project.so: project for project in self.read_eligible_projects()}
        if normalized not in projects:
            raise WorkbookError(f"Order {normalized} is not an eligible DMS Project order.")
        return projects[normalized]

