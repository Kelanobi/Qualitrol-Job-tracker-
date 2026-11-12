from __future__ import annotations

from datetime import date

from app.flow.models import ReviewStatus
from app.phase2.models import EquipmentCandidate, Phase2DryRun
from app.phase2.sync_planner import SyncPlanner, enforce_write_allowlist


class FlowFinalPlanner:
    """Convert explicitly approved Flow review records into the common sync plan."""

    def build(self, run, records, live_rows, processing_date: date) -> Phase2DryRun:
        approved = [(review_id, item) for review_id, item in records if item.status == ReviewStatus.APPROVED]
        if not approved:
            raise ValueError("Approve at least one Flow material row before preparing the Smartsheet preview.")
        customers = {item.line.customer.strip() for _, item in approved if item.line.customer}
        if len(customers) != 1:
            raise ValueError("Approved Flow rows require one consistent commercial customer from the DCL.")
        customer = next(iter(customers))
        sources = [
            (review_id, item, serial_index, serial)
            for review_id, item in approved
            for serial_index, serial in enumerate(item.proposed_serials)
        ]
        equipment = []
        for review_id, item, serial_index, serial in sources:
            overrides = item.proposed_value_overrides.get(str(serial_index), {})
            equipment.append(EquipmentCandidate(
                equipment_type=overrides.get("Equipment", item.category or "Flow Material"),
                serial=overrides.get("Serial No", serial),
                colour=overrides.get("Colour In/Ext") or None,
                serial_source=f"Approved Flow review row {review_id}",
            ))
        plan = SyncPlanner().plan(run.order, equipment, live_rows, customer, processing_date)
        for row, (_, item, serial_index, _) in zip(plan, sources):
            overrides = item.proposed_value_overrides.get(str(serial_index), {})
            for field, value in overrides.items():
                if field in row.proposed_values and value:
                    row.proposed_values[field] = value
                elif value:
                    row.proposed_values[field] = value
            enforce_write_allowlist(row.proposed_values)
            row.serial = row.proposed_values.get("Serial No")
        result = Phase2DryRun(run.order, [], equipment, [], plan)
        result.run_id = run.run_id
        return result
