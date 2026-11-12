from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.phase2.models import LiveSheetRow


class Placement(str, Enum):
    REUSE = "reuse"
    INSERT_BEFORE_FLOW = "insert_before_flow"
    APPEND_FLOW = "append_flow"


@dataclass(frozen=True)
class RowPlacement:
    placement: Placement
    reserved_row_id: int | None = None
    boundary_row_id: int | None = None


PROJECT_START_ROW = 222
FLOW_START_ROW = 431
FORMULA_DEFAULT_FIELDS = {
    "Type", "Forecast Month", "Product", "Cell", "Month", "Week",
    "Modified", "Modified By", ".", "S?", "Assy", "Cell Test", "Order Status",
}
FORMULA_DEFAULT_FIELDS_NORMALIZED = {field.casefold() for field in FORMULA_DEFAULT_FIELDS}


def is_safe_reserved_row(row: LiveSheetRow) -> bool:
    if row.has_attachments or row.has_discussions:
        return False
    if str(row.values.get("SO No.") or "").strip() or str(row.values.get("Serial No") or "").strip():
        return False
    for field, value in row.values.items():
        if str(field).strip().casefold() in FORMULA_DEFAULT_FIELDS_NORMALIZED:
            continue
        if str(value or "").strip():
            return False
    return True


class SheetLayoutPlanner:
    def plan(self, rows: list[LiveSheetRow], order_type: str, required: int) -> list[RowPlacement]:
        if order_type not in {"Project", "Flow"}:
            raise ValueError("Order type must be Project or Flow.")
        ordered = sorted(rows, key=lambda row: row.row_number if row.row_number is not None else 10**12)
        occupied_flow = [row for row in ordered if str(row.values.get("Type") or "").strip() == "Flow"
                         and (str(row.values.get("SO No.") or "").strip() or str(row.values.get("Serial No") or "").strip())]
        first_flow = occupied_flow[0] if occupied_flow else None
        if order_type == "Project":
            occupied_project = [
                row for row in ordered
                if str(row.values.get("Type") or "").strip() == "Project"
                and (str(row.values.get("SO No.") or "").strip() or str(row.values.get("Serial No") or "").strip())
            ]
            last_project_row = max(
                (row.row_number or 0 for row in occupied_project), default=PROJECT_START_ROW - 1,
            )
            slots = [row for row in ordered if is_safe_reserved_row(row)
                     # Rows immediately below the Project block may have either
                     # the formula/default Project label or a genuinely blank
                     # Type cell. Both are valid empty continuation rows.
                     and str(row.values.get("Type") or "").strip() in {"", "Project"}
                     and (row.row_number or 0) > last_project_row
                     and (row.row_number or 0) < FLOW_START_ROW
                     and (first_flow is None or (row.row_number or 0) < (first_flow.row_number or 10**12))]
            placements = [RowPlacement(Placement.REUSE, row.row_id) for row in slots[:required]]
            placements.extend(RowPlacement(Placement.INSERT_BEFORE_FLOW, boundary_row_id=first_flow.row_id if first_flow else None)
                              for _ in range(required - len(placements)))
            return placements
        last_flow_row = max(
            (row.row_number or 0 for row in occupied_flow), default=FLOW_START_ROW - 1,
        )
        slots = [row for row in ordered if is_safe_reserved_row(row)
                 and (row.row_number or 0) > last_flow_row
                 and str(row.values.get("Type") or "").strip() in {"", "Flow"}]
        placements = [RowPlacement(Placement.REUSE, row.row_id) for row in slots[:required]]
        placements.extend(RowPlacement(Placement.APPEND_FLOW) for _ in range(required - len(placements)))
        return placements


def assign_create_placements(plan_rows, rows: list[LiveSheetRow], order_type: str):
    creates = [row for row in plan_rows if row.action.value == "create"]
    placements = SheetLayoutPlanner().plan(rows, order_type, len(creates))
    for plan, placement in zip(creates, placements):
        plan.placement = placement.placement.value
        plan.target_row_id = placement.reserved_row_id
        plan.boundary_row_id = placement.boundary_row_id
    return plan_rows
