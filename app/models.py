from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class Result(str, Enum):
    YES = "Yes"
    MISSING = "Missing"
    NOT_APPLICABLE = "N/A"
    ERROR = "Error"


class OverallStatus(str, Enum):
    FULLY_VERIFIED = "Completed Pass"
    MISSING_DOCUMENTS = "Missing Documents"
    ERROR = "Error"
    NOT_VERIFIED = "Not Yet Verified"


@dataclass(frozen=True)
class Order:
    row: int
    category: str
    value_stream: str
    number: str
    previous_values: dict[str, str | None] = field(default_factory=dict)


@dataclass
class VerificationResult:
    order: Order
    search_mode: str
    started_at: datetime
    completed_at: datetime | None = None
    specs: str = Result.ERROR.value
    qa: str = Result.ERROR.value
    dcl: str = Result.ERROR.value
    drawings: str = Result.ERROR.value
    fat: str = Result.ERROR.value
    excel_update_status: str = "Pending"
    errors: list[str] = field(default_factory=list)
    error_category: str | None = None
    paths: dict[str, Path] = field(default_factory=dict)

    @property
    def overall_status(self) -> str:
        if self.errors or Result.ERROR.value in self.document_values:
            return OverallStatus.ERROR.value
        if Result.MISSING.value in self.document_values:
            return OverallStatus.MISSING_DOCUMENTS.value
        required = [self.specs, self.qa, self.dcl]
        if self.order.category.casefold() == "project":
            required.extend((self.drawings, self.fat))
        if all(value == Result.YES.value for value in required):
            return OverallStatus.FULLY_VERIFIED.value
        return OverallStatus.NOT_VERIFIED.value

    @property
    def document_values(self) -> tuple[str, str, str, str, str]:
        return self.specs, self.qa, self.dcl, self.drawings, self.fat

    def excel_updates(self) -> dict[int, str]:
        values = {11: self.specs, 12: self.qa, 13: self.dcl, 14: self.drawings, 15: self.fat}
        return {column: value for column, value in values.items() if value != Result.ERROR.value}

