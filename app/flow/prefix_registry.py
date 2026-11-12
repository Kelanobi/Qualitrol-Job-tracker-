from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from openpyxl import load_workbook

from app.services.excel_service import WorkbookConflictError, WorkbookError, WorkbookSnapshot


class FlowPrefixRegistry:
    """Shared Flow part-prefix mappings stored on the workbook's second sheet."""

    PART_HEADER = "Part Number"
    PREFIX_HEADER = "Prefix"

    def __init__(self, workbook_path: Path):
        self.workbook_path = Path(workbook_path)

    def _snapshot(self) -> WorkbookSnapshot:
        if not self.workbook_path.is_file():
            raise WorkbookError(f"The workbook could not be found at {self.workbook_path}.")
        stat = self.workbook_path.stat()
        return WorkbookSnapshot(stat.st_size, stat.st_mtime_ns)

    @staticmethod
    def _clean(value: str, label: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-").upper()
        if not cleaned:
            raise ValueError(f"Enter a valid {label}.")
        return cleaned

    @classmethod
    def _mapping_sheet(cls, workbook, create: bool = False):
        if len(workbook.worksheets) < 2:
            if not create:
                return None
            sheet = workbook.create_sheet("Prefixes")
        else:
            sheet = workbook.worksheets[1]
        first = str(sheet.cell(1, 1).value or "").strip()
        second = str(sheet.cell(1, 2).value or "").strip()
        if not first and not second and create:
            sheet.cell(1, 1).value = cls.PART_HEADER
            sheet.cell(1, 2).value = cls.PREFIX_HEADER
        elif first.casefold() != cls.PART_HEADER.casefold() or second.casefold() != cls.PREFIX_HEADER.casefold():
            raise WorkbookError(
                "Sheet 2 must use 'Part Number' in A1 and 'Prefix' in B1 before shared Flow prefixes can be used."
            )
        return sheet

    def read(self) -> dict[str, str]:
        before = self._snapshot()
        workbook = load_workbook(self.workbook_path, read_only=True, data_only=False)
        try:
            sheet = self._mapping_sheet(workbook)
            if sheet is None:
                return {}
            mappings: dict[str, str] = {}
            for row in range(2, sheet.max_row + 1):
                part_raw = str(sheet.cell(row, 1).value or "").strip()
                prefix_raw = str(sheet.cell(row, 2).value or "").strip()
                if not part_raw and not prefix_raw:
                    continue
                part = self._clean(part_raw, f"part number on Sheet 2 row {row}")
                prefix = self._clean(prefix_raw, f"prefix on Sheet 2 row {row}")
                existing = mappings.get(part)
                if existing and existing != prefix:
                    raise WorkbookError(
                        f"Sheet 2 contains conflicting prefixes for {part}: {existing} and {prefix}."
                    )
                mappings[part] = prefix
            if self._snapshot() != before:
                raise WorkbookConflictError("The workbook changed while Sheet 2 prefixes were being read. Retry Phase 2.")
            return mappings
        finally:
            workbook.close()

    def add_or_update(self, part_number: str, prefix: str) -> tuple[str, str]:
        part = self._clean(part_number, "part number")
        cleaned_prefix = self._clean(prefix, "prefix")
        before = self._snapshot()
        workbook = load_workbook(self.workbook_path, data_only=False)
        temp_path = None
        try:
            sheet = self._mapping_sheet(workbook, create=True)
            matches = [
                row for row in range(2, sheet.max_row + 1)
                if str(sheet.cell(row, 1).value or "").strip().casefold() == part.casefold()
            ]
            if len(matches) > 1:
                raise WorkbookError(f"Sheet 2 contains duplicate rows for {part}; resolve them before updating it.")
            row = matches[0] if matches else sheet.max_row + 1
            sheet.cell(row, 1).value = part
            sheet.cell(row, 2).value = cleaned_prefix
            if self._snapshot() != before:
                raise WorkbookConflictError("The workbook changed while the prefix was being prepared. Retry the update.")
            handle, temp_name = tempfile.mkstemp(
                prefix=f".{self.workbook_path.stem}-prefix-",
                suffix=self.workbook_path.suffix,
                dir=self.workbook_path.parent,
            )
            os.close(handle)
            temp_path = Path(temp_name)
            workbook.save(temp_path)
            if self._snapshot() != before:
                raise WorkbookConflictError("The workbook changed before the prefix could be saved. Retry the update.")
            os.replace(temp_path, self.workbook_path)
            temp_path = None
            return part, cleaned_prefix
        except (PermissionError, OSError) as exc:
            raise WorkbookError(
                "The shared workbook is locked or unavailable. Close it on any open laptop and retry. "
                f"Details: {exc}"
            ) from exc
        finally:
            workbook.close()
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
