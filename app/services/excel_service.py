from __future__ import annotations

import os
import tempfile
from copy import copy
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from app.models import Order, VerificationResult


class WorkbookError(RuntimeError):
    pass


class WorkbookConflictError(WorkbookError):
    pass


class MissingAuthenticatedSessionError(WorkbookError):
    pass


@dataclass(frozen=True)
class WorkbookSnapshot:
    size: int
    modified_ns: int


def normalize_identifier(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return format(value, "f").rstrip("0").rstrip(".")
    return str(value).strip()


class ExcelService:
    FAT_COLUMN = 15
    MODIFIED_BY_COLUMN = 16
    def __init__(self, workbook_path: Path, worksheet_name: str = "Sheet1", session_provider=None, audit_recorder=None):
        self.workbook_path = Path(workbook_path)
        self.worksheet_name = worksheet_name
        self.session_provider = session_provider or (lambda: None)
        self.audit_recorder = audit_recorder
        self._orders_cache_snapshot: WorkbookSnapshot | None = None
        self._orders_cache: tuple[Order, ...] = ()
        self._streams_cache: dict[str, str] = {}

    @staticmethod
    def _copy_orders(orders) -> list[Order]:
        return [
            Order(
                row=order.row,
                category=order.category,
                value_stream=order.value_stream,
                number=order.number,
                previous_values=dict(order.previous_values),
            )
            for order in orders
        ]

    def _invalidate_cache(self) -> None:
        self._orders_cache_snapshot = None
        self._orders_cache = ()
        self._streams_cache = {}

    def _authenticated_session(self):
        session = self.session_provider()
        if session is None:
            raise MissingAuthenticatedSessionError(
                "An authenticated employee session is required to modify the workbook."
            )
        return session

    def _audit(self, session, order_number, row, columns, previous, new, saved, error=None):
        if self.audit_recorder:
            self.audit_recorder(
                session, order_number, row, columns, previous, new, saved, error,
            )

    @classmethod
    def _ensure_fat_column(cls, sheet) -> None:
        """Add FAT immediately after Drawings once, preserving the existing audit column."""
        current = str(sheet.cell(1, cls.FAT_COLUMN).value or "").strip().casefold()
        if current == "fat":
            return
        old_width = sheet.column_dimensions[get_column_letter(cls.FAT_COLUMN)].width
        sheet.insert_cols(cls.FAT_COLUMN, 1)
        source = sheet.cell(1, cls.FAT_COLUMN - 1)
        target = sheet.cell(1, cls.FAT_COLUMN)
        target.value = "FAT"
        target._style = copy(source._style)
        target.font = copy(source.font)
        target.fill = copy(source.fill)
        target.border = copy(source.border)
        target.alignment = copy(source.alignment)
        target.number_format = source.number_format
        target.protection = copy(source.protection)
        for row in range(2, sheet.max_row + 1):
            source_cell = sheet.cell(row, cls.FAT_COLUMN - 1)
            fat_cell = sheet.cell(row, cls.FAT_COLUMN)
            fat_cell._style = copy(source_cell._style)
            fat_cell.alignment = copy(source_cell.alignment)
        sheet.column_dimensions[get_column_letter(cls.FAT_COLUMN)].width = (
            sheet.column_dimensions[get_column_letter(cls.FAT_COLUMN - 1)].width
        )
        if old_width is not None:
            sheet.column_dimensions[get_column_letter(cls.MODIFIED_BY_COLUMN)].width = old_width

    @classmethod
    def _write_modified_by(cls, sheet, row: int, email: str) -> None:
        """Keep attribution confined to the Modified by cell on the changed row."""
        cell = sheet.cell(row, cls.MODIFIED_BY_COLUMN)
        cell.value = email
        alignment = copy(cell.alignment)
        alignment.shrinkToFit = True
        cell.alignment = alignment

    def _validate_path(self) -> None:
        if not self.workbook_path.exists():
            raise WorkbookError(f"The workbook could not be found at {self.workbook_path}.")
        if not self.workbook_path.is_file():
            raise WorkbookError(f"The workbook path is not a file: {self.workbook_path}.")

    def snapshot(self) -> WorkbookSnapshot:
        self._validate_path()
        stat = self.workbook_path.stat()
        return WorkbookSnapshot(stat.st_size, stat.st_mtime_ns)

    def read_orders(self) -> list[Order]:
        self._validate_path()
        before = self.snapshot()
        if before == self._orders_cache_snapshot:
            return self._copy_orders(self._orders_cache)
        try:
            workbook = load_workbook(self.workbook_path, read_only=True, data_only=False)
            if self.worksheet_name not in workbook.sheetnames:
                workbook.close()
                raise WorkbookError(f"Worksheet '{self.worksheet_name}' was not found.")
            sheet = workbook[self.worksheet_name]
            orders: list[Order] = []
            streams: dict[str, str] = {}
            for row in range(2, sheet.max_row + 1):
                value_stream = str(sheet.cell(row, 2).value or "").strip()
                number = normalize_identifier(sheet.cell(row, 3).value)
                if number:
                    streams[number] = value_stream
                if value_stream.casefold() != "dms":
                    continue
                category = str(sheet.cell(row, 1).value or "").strip()
                if not number:
                    workbook.close()
                    raise WorkbookError(f"DMS row {row} has an empty or invalid order number in column C.")
                orders.append(Order(
                    row=row,
                    category=category,
                    value_stream=value_stream,
                    number=number,
                    previous_values={
                        "specs": sheet.cell(row, 11).value,
                        "qa": sheet.cell(row, 12).value,
                        "dcl": sheet.cell(row, 13).value,
                        "drawings": sheet.cell(row, 14).value,
                        "fat": sheet.cell(row, 15).value if str(sheet.cell(1, 15).value or "").strip().casefold() == "fat" else None,
                    },
                ))
            workbook.close()
            if self.snapshot() == before:
                self._orders_cache_snapshot = before
                self._orders_cache = tuple(orders)
                self._streams_cache = streams
            return orders
        except WorkbookError:
            raise
        except (OSError, PermissionError, InvalidFileException) as exc:
            raise WorkbookError(f"The workbook could not be read safely: {exc}") from exc

    def find_orders(self, numbers: list[str]) -> tuple[list[Order], dict[str, str]]:
        requested = [normalize_identifier(number) for number in numbers]
        self._validate_path()
        before = self.snapshot()
        by_number: dict[str, Order] = {}
        any_stream: dict[str, str] = {}
        found: list[Order] = []
        errors: dict[str, str] = {}
        if before == self._orders_cache_snapshot:
            by_number = {order.number: order for order in self._copy_orders(self._orders_cache)}
            any_stream = dict(self._streams_cache)
        else:
            try:
                workbook = load_workbook(self.workbook_path, read_only=True, data_only=False)
                if self.worksheet_name not in workbook.sheetnames:
                    workbook.close()
                    raise WorkbookError(f"Worksheet '{self.worksheet_name}' was not found.")
                sheet = workbook[self.worksheet_name]
                for row in range(2, sheet.max_row + 1):
                    number = normalize_identifier(sheet.cell(row, 3).value)
                    if not number:
                        continue
                    stream = str(sheet.cell(row, 2).value or "").strip()
                    any_stream[number] = stream
                    if stream.casefold() == "dms":
                        by_number[number] = Order(
                            row=row,
                            category=str(sheet.cell(row, 1).value or "").strip(),
                            value_stream=stream,
                            number=number,
                            previous_values={
                                "specs": sheet.cell(row, 11).value,
                                "qa": sheet.cell(row, 12).value,
                                "dcl": sheet.cell(row, 13).value,
                                "drawings": sheet.cell(row, 14).value,
                                "fat": sheet.cell(row, 15).value if str(sheet.cell(1, 15).value or "").strip().casefold() == "fat" else None,
                            },
                        )
                workbook.close()
                if self.snapshot() == before:
                    self._orders_cache_snapshot = before
                    self._orders_cache = tuple(by_number.values())
                    self._streams_cache = dict(any_stream)
            except WorkbookError:
                raise
            except (OSError, PermissionError, InvalidFileException) as exc:
                raise WorkbookError(f"The workbook could not be read safely: {exc}") from exc

        for number in requested:
            if number in by_number:
                found.append(by_number[number])
            elif number in any_stream and any_stream[number].casefold() == "ip":
                errors[number] = (
                    f"Order {number} belongs to the IP value stream and cannot be processed by DMS Order Tracker."
                )
            else:
                errors[number] = f"Order {number} was not found in the Excel workbook."
        return found, errors

    def update_results(self, results: list[VerificationResult]) -> None:
        if not results:
            return
        session = self._authenticated_session()
        audits = [(result.order.number, None, [], {}, {}) for result in results]
        try:
            before = self.snapshot()
            workbook = load_workbook(self.workbook_path, data_only=False)
            sheet = workbook[self.worksheet_name]
            self._ensure_fat_column(sheet)
            current_by_number: dict[str, int] = {}
            for row in range(2, sheet.max_row + 1):
                if str(sheet.cell(row, 2).value or "").strip().casefold() == "dms":
                    current_by_number[normalize_identifier(sheet.cell(row, 3).value)] = row

            audits = []
            for result in results:
                row = current_by_number.get(result.order.number)
                if row is None:
                    raise WorkbookConflictError(
                        f"Order {result.order.number} is no longer present as a DMS row. No changes were saved."
                    )
                previous = {}
                proposed = {}
                changed = []
                for column, value in result.excel_updates().items():
                    current = sheet.cell(row, column).value
                    if current != value:
                        previous[column] = current
                        proposed[column] = value
                        changed.append(column)
                        sheet.cell(row, column).value = value
                if changed:
                    previous[self.MODIFIED_BY_COLUMN] = sheet.cell(row, self.MODIFIED_BY_COLUMN).value
                    proposed[self.MODIFIED_BY_COLUMN] = session.email
                    self._write_modified_by(sheet, row, session.email)
                audits.append((
                    result.order.number, row,
                    changed + ([self.MODIFIED_BY_COLUMN] if changed else []), previous, proposed,
                ))

            if not any(columns for _order, _row, columns, _previous, _proposed in audits):
                workbook.close()
                for order_number, row, columns, previous, proposed in audits:
                    self._audit(session, order_number, row, columns, previous, proposed, True)
                return

            if self.snapshot() != before:
                raise WorkbookConflictError(
                    "The workbook changed while verification was running. No changes were saved; reload and retry."
                )
            handle, temp_name = tempfile.mkstemp(
                prefix=f".{self.workbook_path.stem}-", suffix=self.workbook_path.suffix, dir=self.workbook_path.parent
            )
            os.close(handle)
            temp_path = Path(temp_name)
            try:
                workbook.save(temp_path)
                if self.snapshot() != before:
                    raise WorkbookConflictError(
                        "The workbook changed before the update could be committed. No changes were saved."
                    )
                os.replace(temp_path, self.workbook_path)
                self._invalidate_cache()
            finally:
                workbook.close()
                temp_path.unlink(missing_ok=True)
            for order_number, row, columns, previous, proposed in audits:
                self._audit(session, order_number, row, columns, previous, proposed, True)
        except WorkbookError:
            error = "Workbook conflict or access failure."
            for order_number, row, columns, previous, proposed in audits:
                self._audit(session, order_number, row, columns, previous, proposed, False, error)
            raise
        except (PermissionError, OSError) as exc:
            for order_number, row, columns, previous, proposed in audits:
                self._audit(session, order_number, row, columns, previous, proposed, False, str(exc))
            raise WorkbookError(
                "The Excel workbook is currently locked or cannot be safely updated. "
                f"Please close it and retry. Details: {exc}"
            ) from exc
        except Exception as exc:
            for order_number, row, columns, previous, proposed in audits:
                self._audit(session, order_number, row, columns, previous, proposed, False, str(exc))
            raise WorkbookError(f"The workbook update failed safely: {exc}") from exc

    def change_order_category(self, order_number: str, expected: str, replacement: str) -> None:
        """Safely change only column A for one DMS order after an explicit UI confirmation."""
        session = self._authenticated_session()
        workbook = None
        temp_path = None
        row = None
        previous = {}
        proposed = {}
        try:
            before = self.snapshot()
            workbook = load_workbook(self.workbook_path, data_only=False)
            sheet = workbook[self.worksheet_name]
            self._ensure_fat_column(sheet)
            matches = [
                row for row in range(2, sheet.max_row + 1)
                if normalize_identifier(sheet.cell(row, 3).value) == normalize_identifier(order_number)
                and str(sheet.cell(row, 2).value or "").strip().casefold() == "dms"
            ]
            if len(matches) != 1:
                raise WorkbookConflictError(
                    f"Expected exactly one DMS row for order {order_number}; found {len(matches)}. No change was made."
                )
            row = matches[0]
            current = str(sheet.cell(row, 1).value or "").strip()
            if current.casefold() != expected.casefold():
                raise WorkbookConflictError(
                    f"Order {order_number} is now classified as {current or 'blank'}, not {expected}. Refresh and retry."
                )
            if current == replacement:
                self._audit(session, order_number, row, [], {}, {}, True)
                return
            previous = {1: sheet.cell(row, 1).value, self.MODIFIED_BY_COLUMN: sheet.cell(row, self.MODIFIED_BY_COLUMN).value}
            proposed = {1: replacement, self.MODIFIED_BY_COLUMN: session.email}
            sheet.cell(row, 1).value = replacement
            self._write_modified_by(sheet, row, session.email)
            if self.snapshot() != before:
                raise WorkbookConflictError("The workbook changed while the classification was being prepared. No change was made.")
            handle, temp_name = tempfile.mkstemp(
                prefix=f".{self.workbook_path.stem}-", suffix=self.workbook_path.suffix,
                dir=self.workbook_path.parent,
            )
            os.close(handle)
            temp_path = Path(temp_name)
            workbook.save(temp_path)
            if self.snapshot() != before:
                raise WorkbookConflictError("The workbook changed before the classification could be saved. No change was made.")
            os.replace(temp_path, self.workbook_path)
            self._invalidate_cache()
            temp_path = None
            self._audit(session, order_number, row, [1], previous, proposed, True)
        except WorkbookError:
            self._audit(session, order_number, row, [1] if previous else [], previous, proposed, False, "Workbook conflict or access failure.")
            raise
        except (PermissionError, OSError) as exc:
            self._audit(session, order_number, row, [1] if previous else [], previous, proposed, False, str(exc))
            raise WorkbookError(f"The workbook is locked or unavailable; the classification was not changed: {exc}") from exc
        finally:
            if workbook is not None:
                workbook.close()
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

