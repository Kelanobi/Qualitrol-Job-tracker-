from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table, TableStyleInfo

from app.config import REPORT_DIR


class MonthlyReportService:
    def __init__(self, history, output_dir: Path = REPORT_DIR, orders_provider=None):
        self.history = history
        self.output_dir = Path(output_dir)
        self.orders_provider = orders_provider

    def path_for(self, year: int, month: int) -> Path:
        return self.output_dir / f"{calendar.month_name[month]} Monthly Excel Report.xlsx"

    def generate(self, year: int, month: int, overwrite: bool = True) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(year, month)
        if path.exists() and not overwrite:
            return path
        rows = self.history.month_rows(year, month)
        if self.orders_provider:
            by_number = {str(row["order_number"]): row for row in rows}
            expanded = []
            for order in self.orders_provider():
                row = dict(by_number.get(order.number) or {
                    "order_number": order.number, "specs": "Missing", "qa": "Missing", "dcl": "Missing",
                    "drawings": "Missing", "fat": "Missing", "completed_at": None,
                    "employee_email": None, "error_message": None,
                })
                row["order_category"] = order.category
                if order.category.casefold() == "flow": row["drawings"] = row["fat"] = "N/A"
                row["overall_status"] = self.history.effective_status(row)
                expanded.append(row)
            rows = expanded
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Monthly Results"
        sheet.sheet_view.showGridLines = False
        red = "E30613"; navy = "102A4C"; pale = "F2F5F8"; green = "DDF4E7"; amber = "FFF0CC"
        sheet.merge_cells("A2:K2")
        sheet["A2"] = f"{calendar.month_name[month]} {year} DMS Order Tracker Report"
        sheet["A2"].font = Font(name="Arial", size=16, bold=True, color=navy)
        sheet["A2"].alignment = Alignment(horizontal="left")
        sheet["A3"] = "Generated"
        sheet["B3"] = datetime.now()
        sheet["B3"].number_format = "dd mmm yyyy hh:mm"
        completed = sum(row["overall_status"] == "Completed Pass" for row in rows)
        sheet["A5"] = "Total Orders"; sheet["B5"] = len(rows)
        sheet["D5"] = "Completed Pass"; sheet["E5"] = completed
        sheet["G5"] = "Missing Documents"; sheet["H5"] = len(rows) - completed
        for cell in ("A5", "D5", "G5"):
            sheet[cell].font = Font(name="Arial", bold=True, color="FFFFFF")
            sheet[cell].fill = PatternFill("solid", fgColor=navy)
        for cell in ("B5", "E5", "H5"):
            sheet[cell].font = Font(name="Arial", size=13, bold=True, color=navy)
            sheet[cell].fill = PatternFill("solid", fgColor=pale)
            sheet[cell].alignment = Alignment(horizontal="center")
        headers = [
            "Order Number", "Type", "Specs", "QA", "DCL", "Drawings", "FAT",
            "Result", "Checked", "Checked By", "Diagnostic Notes",
        ]
        header_row = 8
        for column, value in enumerate(headers, 1):
            cell = sheet.cell(header_row, column, value)
            cell.font = Font(name="Arial", bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=red)
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for row_index, row in enumerate(rows, header_row + 1):
            values = [
                row["order_number"], row["order_category"], row["specs"], row["qa"], row["dcl"],
                row["drawings"], row["fat"], row["overall_status"],
                datetime.fromisoformat(str(row["completed_at"])) if row.get("completed_at") else None,
                row.get("employee_email") or "",
                row.get("error_message") or "",
            ]
            for column, value in enumerate(values, 1):
                cell = sheet.cell(row_index, column, value)
                cell.font = Font(name="Arial", size=10, color="1B2430")
                cell.alignment = Alignment(vertical="top", wrap_text=column == 11)
            sheet.cell(row_index, 9).number_format = "dd mmm yyyy hh:mm"
            result_cell = sheet.cell(row_index, 8)
            result_cell.fill = PatternFill(
                "solid", fgColor=green if row["overall_status"] == "Completed Pass" else amber
            )
        last_row = max(header_row + 1, header_row + len(rows))
        if not rows:
            sheet.cell(header_row + 1, 1, "No verification results were recorded for this month.")
        else:
            table = Table(displayName="MonthlyVerificationResults", ref=f"A{header_row}:K{last_row}")
            table.tableStyleInfo = TableStyleInfo(
                name="TableStyleMedium2", showFirstColumn=False, showLastColumn=False,
                showRowStripes=True, showColumnStripes=False,
            )
            sheet.add_table(table)
        sheet.freeze_panes = f"A{header_row + 1}"
        widths = {"A": 16, "B": 12, "C": 12, "D": 18, "E": 12, "F": 13, "G": 22,
                  "H": 20, "I": 21, "J": 30, "K": 52}
        for column, width in widths.items():
            sheet.column_dimensions[column].width = width
        sheet.row_dimensions[2].height = 26
        sheet.row_dimensions[header_row].height = 24
        sheet.auto_filter.ref = f"A{header_row}:K{last_row}"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.print_title_rows = f"1:{header_row}"
        workbook.save(path)
        return path

    def generate_due(self, now: datetime | None = None) -> Path | None:
        now = now or datetime.now()
        last_day = calendar.monthrange(now.year, now.month)[1]
        if now.day == last_day and now.hour >= 12:
            target = now
        else:
            target = now.replace(day=1) - timedelta(days=1)
        path = self.path_for(target.year, target.month)
        if path.exists() and self._is_report_for(path, target.year, target.month):
            return path
        return self.generate(target.year, target.month, overwrite=True)

    @staticmethod
    def _is_report_for(path: Path, year: int, month: int) -> bool:
        """Return true only when the existing same-name report is for this period."""
        try:
            workbook = load_workbook(path, read_only=True, data_only=True)
            try:
                title = str(workbook["Monthly Results"]["A2"].value or "")
            finally:
                workbook.close()
            return title == f"{calendar.month_name[month]} {year} DMS Order Tracker Report"
        except Exception:
            return False
