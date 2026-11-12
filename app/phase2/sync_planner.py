from __future__ import annotations

from collections import defaultdict
from datetime import date

from app.phase2.models import (
    EquipmentCandidate, IssueSeverity, LiveSheetRow, PlanAction, ProjectOrder,
    SyncPlanRow, ValidationIssue,
)


APPROVED_FIELDS = frozenset({
    "Forecast Month", "Type", "SO No.", "Customer", "Incoterms", "Serial No", "Colour In/Ext",
})
FORBIDDEN_FIELDS = frozenset({"Product", "Cell", "Plinth Colour"})


def enforce_write_allowlist(values: dict[str, str]) -> None:
    disallowed = set(values) - APPROVED_FIELDS
    if disallowed:
        raise ValueError(f"Phase 2 attempted to write unapproved Smartsheet fields: {sorted(disallowed)}")


class SyncPlanner:
    def plan(
        self,
        order: ProjectOrder,
        equipment: list[EquipmentCandidate],
        live_rows: list[LiveSheetRow],
        commercial_customer: str | None,
        processing_date: date,
    ) -> list[SyncPlanRow]:
        index: dict[tuple[str, str], list[LiveSheetRow]] = defaultdict(list)
        for row in live_rows:
            so = str(row.values.get("SO No.") or "").strip().casefold()
            serial = str(row.values.get("Serial No") or "").strip().casefold()
            if so and serial:
                index[(so, serial)].append(row)

        plan: list[SyncPlanRow] = []
        for candidate in equipment:
            issues = list(candidate.issues)
            if not candidate.serial:
                issues.append(ValidationIssue(
                    code="MISSING_SERIAL",
                    severity=IssueSeverity.BLOCKING,
                    message=f"{candidate.equipment_type} cannot be created without a verified serial.",
                    equipment_type=candidate.equipment_type,
                ))
            if not commercial_customer:
                issues.append(ValidationIssue(
                    code="MISSING_COMMERCIAL_CUSTOMER",
                    severity=IssueSeverity.BLOCKING,
                    message="Commercial customer evidence is missing; workbook technical/customer data is not substituted.",
                    equipment_type=candidate.equipment_type,
                    serial=candidate.serial,
                ))
            if not order.term:
                issues.append(ValidationIssue(
                    code="MISSING_INCOTERMS",
                    severity=IssueSeverity.BLOCKING,
                    message="Workbook Term is missing; Incoterms cannot be invented.",
                    equipment_type=candidate.equipment_type,
                    serial=candidate.serial,
                ))

            values = {
                "Forecast Month": processing_date.strftime("%d-%b"),
                "Type": order.category,
                "SO No.": order.so,
            }
            if commercial_customer:
                values["Customer"] = commercial_customer
            if order.term:
                values["Incoterms"] = order.term
            if candidate.serial:
                values["Serial No"] = candidate.serial
            if candidate.colour and candidate.equipment_type not in {"PCU", "SIU", "SDI", "SER"}:
                values["Colour In/Ext"] = candidate.colour
            enforce_write_allowlist(values)

            matches = index.get((order.so.casefold(), (candidate.serial or "").casefold()), [])
            if len(matches) > 1:
                issues.append(ValidationIssue(
                    code="DUPLICATE_LIVE_ROWS",
                    severity=IssueSeverity.BLOCKING,
                    message=f"Multiple live rows match SO {order.so} and serial {candidate.serial}.",
                    equipment_type=candidate.equipment_type,
                    serial=candidate.serial,
                ))

            existing = matches[0] if len(matches) == 1 else None
            changes: dict[str, tuple[str | None, str]] = {}
            if existing:
                for field, proposed in values.items():
                    current = existing.values.get(field)
                    if str(current or "").strip() != proposed:
                        changes[field] = (current, proposed)
            # Production Smartsheet is append-only. A matching identity proves
            # the row already exists; it is reported as unchanged even if newer
            # extracted values differ. Excel remains the mutable source for
            # document-verification results.
            action = PlanAction.BLOCKED if any(issue.severity == IssueSeverity.BLOCKING for issue in issues) else (
                PlanAction.CREATE if existing is None else PlanAction.UNCHANGED
            )
            plan.append(SyncPlanRow(
                action=action,
                equipment_type=candidate.equipment_type,
                serial=candidate.serial,
                existing_row_id=existing.row_id if existing else None,
                proposed_values=values,
                changes=changes,
                issues=issues,
            ))
        return plan

