from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class IssueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class PlanAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    BLOCKED = "blocked"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class ProjectOrder:
    row: int
    so: str
    term: str | None
    workbook_customer: str | None
    category: str = "Project"
    value_stream: str = "DMS"


@dataclass(frozen=True)
class DocumentRecord:
    category: str
    path: Path
    filename: str
    extension: str
    size: int
    modified_at: datetime
    sha256: str | None
    detected_type: str
    revision: str | None = None
    order_reference: str | None = None
    parse_status: str = "pending"
    error: str | None = None


@dataclass(frozen=True)
class Evidence:
    field: str
    value: str
    source_file: str
    location: str | None
    extraction_method: str
    confidence: float
    revision: str | None = None
    validation_state: str = "candidate"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: IssueSeverity
    message: str
    equipment_type: str | None = None
    serial: str | None = None


@dataclass
class EquipmentCandidate:
    equipment_type: str
    quantity: int = 1
    serial: str | None = None
    colour: str | None = None
    serial_source: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(issue.severity == IssueSeverity.BLOCKING for issue in self.issues)


@dataclass(frozen=True)
class LiveSheetRow:
    row_id: int
    values: dict[str, str | None]
    row_number: int | None = None
    has_attachments: bool = False
    has_discussions: bool = False


@dataclass
class SyncPlanRow:
    action: PlanAction
    equipment_type: str
    serial: str | None
    existing_row_id: int | None
    proposed_values: dict[str, str]
    changes: dict[str, tuple[str | None, str]]
    issues: list[ValidationIssue] = field(default_factory=list)
    placement: str | None = None
    target_row_id: int | None = None
    boundary_row_id: int | None = None

    @property
    def writable(self) -> bool:
        return self.action in {PlanAction.CREATE, PlanAction.UPDATE} and not any(
            issue.severity == IssueSeverity.BLOCKING for issue in self.issues
        )


@dataclass
class Phase2DryRun:
    order: ProjectOrder
    documents: list[DocumentRecord]
    equipment: list[EquipmentCandidate]
    issues: list[ValidationIssue]
    plan_rows: list[SyncPlanRow]
    evidence: list[Evidence] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    run_id: int | None = None

