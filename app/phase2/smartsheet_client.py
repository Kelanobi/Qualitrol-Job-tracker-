from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.phase2.models import IssueSeverity, LiveSheetRow, PlanAction, SyncPlanRow, ValidationIssue
from app.phase2.sync_planner import APPROVED_FIELDS, enforce_write_allowlist


class SmartsheetConfigurationError(RuntimeError):
    pass


class SheetReader(Protocol):
    def get_sheet(self, sheet_id: str) -> Any: ...


@dataclass(frozen=True)
class SheetColumn:
    column_id: int
    title: str
    column_type: str | None
    options: tuple[str, ...]
    formula: str | None
    locked: bool


@dataclass(frozen=True)
class SheetSnapshot:
    sheet_id: str
    columns: dict[str, SheetColumn]
    rows: list[LiveSheetRow]


def _value(item: Any, name: str, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


class ReadOnlySmartsheetClient:
    """Fetches schema/rows only. This class deliberately exposes no write operation."""

    def __init__(self, sheet_id: str, reader: SheetReader):
        if not sheet_id.strip():
            raise SmartsheetConfigurationError("A Smartsheet sheet ID is required.")
        self.sheet_id = sheet_id.strip()
        self.reader = reader

    def snapshot(self) -> SheetSnapshot:
        sheet = self.reader.get_sheet(self.sheet_id)
        columns_by_id: dict[int, SheetColumn] = {}
        columns_by_title: dict[str, SheetColumn] = {}
        duplicate_titles: set[str] = set()
        for raw in _value(sheet, "columns", []) or []:
            title = str(_value(raw, "title", "")).strip()
            column_id = int(_value(raw, "id"))
            column = SheetColumn(
                column_id=column_id,
                title=title,
                column_type=(str(_value(raw, "type")) if _value(raw, "type") is not None else None),
                options=tuple(str(option) for option in (_value(raw, "options", []) or [])),
                formula=_value(raw, "formula"),
                locked=bool(_value(raw, "locked", False)),
            )
            if title in columns_by_title:
                duplicate_titles.add(title)
            columns_by_id[column_id] = column
            columns_by_title[title] = column

        missing = APPROVED_FIELDS - set(columns_by_title)
        if missing:
            raise SmartsheetConfigurationError(
                f"The live sheet is missing approved columns: {sorted(missing)}"
            )
        ambiguous = APPROVED_FIELDS & duplicate_titles
        if ambiguous:
            raise SmartsheetConfigurationError(
                f"The live sheet has duplicate approved column names: {sorted(ambiguous)}"
            )
        unusable = sorted(
            title for title in APPROVED_FIELDS
            if columns_by_title[title].locked or columns_by_title[title].formula
        )
        if unusable:
            raise SmartsheetConfigurationError(
                f"Approved columns are locked or formula-driven and cannot be safely written: {unusable}"
            )

        rows: list[LiveSheetRow] = []
        for raw_row in _value(sheet, "rows", []) or []:
            values: dict[str, str | None] = {}
            for cell in _value(raw_row, "cells", []) or []:
                column = columns_by_id.get(int(_value(cell, "column_id")))
                if column:
                    raw_value = _value(cell, "value")
                    values[column.title] = None if raw_value is None else str(raw_value)
            rows.append(LiveSheetRow(
                row_id=int(_value(raw_row, "id")), values=values,
                row_number=_value(raw_row, "row_number"),
                has_attachments=bool(_value(raw_row, "attachments", [])),
                has_discussions=bool(_value(raw_row, "discussions", [])),
            ))
        return SheetSnapshot(self.sheet_id, columns_by_title, rows)


class OfficialSdkSheetReader:
    """Lazy official-SDK adapter; the token is held in memory and never logged."""

    def __init__(self, token: str):
        if not token.strip():
            raise SmartsheetConfigurationError("A Smartsheet API token is required.")
        try:
            import smartsheet
        except ImportError as exc:
            raise SmartsheetConfigurationError(
                "The official Smartsheet SDK is not installed. Install project requirements first."
            ) from exc
        self._client = smartsheet.Smartsheet(token.strip())
        self._client.errors_as_exceptions(True)

    def get_sheet(self, sheet_id: str):
        return self._client.Sheets.get_sheet(sheet_id)


class OfficialSdkSyncGateway:
    def __init__(self, sheet_id: str, token: str, initial_snapshot: SheetSnapshot | None = None):
        self.sheet_id = sheet_id
        self.reader = OfficialSdkSheetReader(token)
        self._client = self.reader._client
        self._cached_snapshot: SheetSnapshot | None = initial_snapshot
        self._rows_by_id: dict[int, dict[str, str | None]] = {
            row.row_id: dict(row.values) for row in initial_snapshot.rows
        } if initial_snapshot else {}
        self._pending_updates: dict[int, dict[str, str]] = {}

    def _snapshot(self, refresh: bool = False) -> SheetSnapshot:
        if refresh or self._cached_snapshot is None:
            self._cached_snapshot = ReadOnlySmartsheetClient(self.sheet_id, self.reader).snapshot()
            self._rows_by_id = {
                row.row_id: dict(row.values) for row in self._cached_snapshot.rows
            }
        return self._cached_snapshot

    def snapshot(self, refresh: bool = False) -> SheetSnapshot:
        return self._snapshot(refresh)

    def cached_row(self, row_id: int) -> dict[str, str | None] | None:
        self._snapshot()
        values = self._rows_by_id.get(row_id)
        return dict(values) if values is not None else None

    def read_rows(self, row_ids: list[int]) -> dict[int, dict[str, str | None]]:
        """Verify many writes with one authoritative sheet read."""
        self._flush_updates()
        self._snapshot(refresh=True)
        return {
            row_id: dict(self._rows_by_id[row_id])
            for row_id in row_ids if row_id in self._rows_by_id
        }

    def find_rows(self, so: str, serial: str) -> list[tuple[int, dict[str, str | None]]]:
        self._snapshot()
        return [
            (row_id, values) for row_id, values in self._rows_by_id.items()
            if str(values.get("SO No.") or "").strip().casefold() == so.strip().casefold()
            and str(values.get("Serial No") or "").strip().casefold() == serial.strip().casefold()
        ]

    def _flush_updates(self) -> None:
        if not self._pending_updates:
            return
        pending = list(self._pending_updates.items())
        try:
            # Smartsheet accepts up to 500 rows per request. Keeping the chunk
            # bounded also makes very large batches predictable.
            for start in range(0, len(pending), 500):
                chunk = pending[start:start + 500]
                payload = [
                    {"id": row_id, "cells": self._cells(values)}
                    for row_id, values in chunk
                ]
                self._client.Sheets.update_rows(self.sheet_id, payload)
        except Exception:
            self._cached_snapshot = None
            self._rows_by_id = {}
            raise
        finally:
            self._pending_updates.clear()

    def _cells(self, values: dict[str, str]) -> list[dict]:
        enforce_write_allowlist(values)
        snapshot = self._snapshot()
        return [
            {"columnId": snapshot.columns[title].column_id, "value": value, "strict": True}
            for title, value in values.items()
        ]

    def create_row(
        self, values: dict[str, str], placement: str | None = None,
        boundary_row_id: int | None = None,
    ) -> int:
        # Do not risk discarding buffered reserved-row updates if a later append
        # has an unknown outcome.
        self._flush_updates()
        row = {"cells": self._cells(values)}
        if placement == "insert_before_flow" and boundary_row_id is not None:
            row.update({"siblingId": boundary_row_id, "above": True})
        else:
            row["toBottom"] = True
        try:
            response = self._client.Sheets.add_rows(self.sheet_id, [row])
        except Exception:
            # The outcome may be unknown; force the executor's reconciliation
            # lookup to read fresh server state instead of the pre-write cache.
            self._cached_snapshot = None
            self._rows_by_id = {}
            raise
        created = response.result
        if isinstance(created, list):
            created = created[0]
        row_id = int(created.id)
        self._rows_by_id[row_id] = dict(values)
        return row_id

    def update_row(self, row_id: int, values: dict[str, str]) -> None:
        enforce_write_allowlist(values)
        self._pending_updates.setdefault(row_id, {}).update(values)
        self._rows_by_id.setdefault(row_id, {}).update(values)

    def read_row(self, row_id: int) -> dict[str, str | None]:
        try:
            raw = self._client.Sheets.get_row(self.sheet_id, row_id)
        except Exception:
            # Newly inserted rows can briefly return 404 from get_row even
            # though a full sheet read already contains them. Use that
            # authoritative snapshot for immediate readback verification.
            self._snapshot(refresh=True)
            if row_id in self._rows_by_id:
                return dict(self._rows_by_id[row_id])
            raise
        snapshot = self._snapshot()
        titles = {column.column_id: title for title, column in snapshot.columns.items()}
        values = {
            titles[int(cell.column_id)]: None if cell.value is None else str(cell.value)
            for cell in raw.cells if int(cell.column_id) in titles
        }
        self._rows_by_id[row_id] = dict(values)
        return values


class OfficialSdkSchemaManager:
    """Narrow schema writer used only for explicitly approved dropdown options."""

    def __init__(self, sheet_id: str, token: str):
        self.sheet_id = sheet_id
        self.reader = OfficialSdkSheetReader(token)

    def ensure_picklist_option(self, title: str, value: str) -> bool:
        return self.ensure_picklist_options({title: {value}})

    def ensure_picklist_options(self, requested: dict[str, set[str]]) -> bool:
        """Add all missing dropdown values with one schema read and verification read."""
        requested = {
            title: {str(value).strip() for value in values if str(value).strip()}
            for title, values in requested.items() if values
        }
        if not requested:
            return False
        sheet = self.reader.get_sheet(self.sheet_id)
        changed = False
        for title, values in requested.items():
            matches = [column for column in sheet.columns if column.title == title]
            if len(matches) != 1:
                raise SmartsheetConfigurationError(
                    f"Expected exactly one live column named {title!r}; found {len(matches)}."
                )
            column = matches[0]
            if str(column.type_) != "PICKLIST":
                raise SmartsheetConfigurationError(f"{title!r} is not a live PICKLIST column.")
            options = list(column.options or [])
            missing = sorted(values - set(options), key=str.casefold)
            if not missing:
                continue
            self.reader._client.Sheets.update_column(
                self.sheet_id, column.id, {"type": "PICKLIST", "options": options + missing},
            )
            changed = True
        if not changed:
            return False
        verified = self.reader.get_sheet(self.sheet_id)
        for title, values in requested.items():
            verified_column = next(item for item in verified.columns if item.title == title)
            missing = values - set(verified_column.options or [])
            if missing:
                raise RuntimeError(
                    f"Smartsheet did not retain the new {title} option(s): {sorted(missing)}."
                )
        return True


def missing_picklist_options(
    plan_rows: list[SyncPlanRow], snapshot: SheetSnapshot,
) -> dict[str, set[str]]:
    """Collect proposed values absent from editable live PICKLIST columns."""
    missing: dict[str, set[str]] = {}
    for row in plan_rows:
        for field, proposed in row.proposed_values.items():
            column = snapshot.columns.get(field)
            if (
                column is not None and column.column_type == "PICKLIST"
                and proposed not in column.options
            ):
                missing.setdefault(field, set()).add(proposed)
    return missing


def validate_plan_against_live_schema(
    plan_rows: list[SyncPlanRow], snapshot: SheetSnapshot,
) -> list[SyncPlanRow]:
    """Validate columns; missing picklist options are created by the controller."""
    for row in plan_rows:
        for field, proposed in row.proposed_values.items():
            column = snapshot.columns.get(field)
            if column is None:
                row.issues.append(ValidationIssue(
                    code="LIVE_COLUMN_MISSING", severity=IssueSeverity.BLOCKING,
                    message=f"The live Smartsheet column {field!r} is missing.",
                    equipment_type=row.equipment_type, serial=row.serial,
                ))
                row.action = PlanAction.BLOCKED
                continue
            if column.column_type == "PICKLIST" and proposed not in column.options:
                row.issues.append(ValidationIssue(
                    code="PICKLIST_OPTION_PENDING", severity=IssueSeverity.WARNING,
                    message=(
                        f"{field} value {proposed!r} is not yet in the live dropdown; "
                        "the app will add it automatically before synchronization."
                    ),
                    equipment_type=row.equipment_type, serial=row.serial,
                ))
    return plan_rows
