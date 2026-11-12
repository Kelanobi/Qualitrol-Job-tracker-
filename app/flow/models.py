from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class ReviewStatus(str, Enum):
    RECOGNISED = "recognised"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    EXCLUDED = "excluded"
    BLOCKED = "blocked"


@dataclass
class FlowDclLine:
    so: str
    customer: str | None
    source_file: str
    page: int
    line_reference: str | None
    part_code: str | None
    description: str
    quantity: Decimal | None
    unit: str | None
    explicit_serials: list[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class FlowReviewItem:
    line: FlowDclLine
    category: str | None
    serial_pattern: str | None
    proposed_serials: list[str]
    represented_quantity: Decimal | None
    status: ReviewStatus
    issues: list[str] = field(default_factory=list)
    catalogue_key: str | None = None
    catalogue_version: int = 1
    approved: bool = False
    excluded_reason: str | None = None
    proposed_value_overrides: dict[str, dict[str, str]] = field(default_factory=dict)

