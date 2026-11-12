from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.flow.models import FlowDclLine, FlowReviewItem, ReviewStatus
from app.flow.planner import FlowFinalPlanner
from app.phase2.models import LiveSheetRow, ProjectOrder


def item(status=ReviewStatus.APPROVED):
    line = FlowDclLine("50577574", "ACME", "dcl.pdf", 2, None, "AE02909", "WIN", Decimal("1"), "ea")
    return FlowReviewItem(line, "WIN", "{SO}-WIN-{NN}", ["50577574-WIN-01"], Decimal("1"), status, approved=status == ReviewStatus.APPROVED)


def test_approved_flow_rows_produce_flow_plan_without_colour():
    run = SimpleNamespace(order=ProjectOrder(1, "50577574", "DAP", None, category="Flow"), run_id=7)
    result = FlowFinalPlanner().build(run, [(12, item())], [], date(2026, 10, 21))
    assert result.run_id == 7
    assert result.plan_rows[0].proposed_values == {
        "Forecast Month": "21-Oct", "Type": "Flow", "SO No.": "50577574",
        "Customer": "ACME", "Incoterms": "DAP", "Serial No": "50577574-WIN-01",
    }


def test_preview_requires_at_least_one_approved_flow_row():
    run = SimpleNamespace(order=ProjectOrder(1, "50577574", "DAP", None, category="Flow"), run_id=7)
    with pytest.raises(ValueError, match="Approve at least one"):
        FlowFinalPlanner().build(run, [(12, item(ReviewStatus.NEEDS_REVIEW))], [], date.today())


def test_approved_flow_rows_can_preview_while_other_rows_still_need_review():
    run = SimpleNamespace(order=ProjectOrder(1, "50577574", "DAP", None, category="Flow"), run_id=7)
    pending = item(ReviewStatus.NEEDS_REVIEW)
    pending.proposed_serials = ["50577574-PENDING-01"]

    result = FlowFinalPlanner().build(
        run, [(12, item()), (13, pending)], [], date(2026, 10, 21),
    )

    assert [row.serial for row in result.plan_rows] == ["50577574-WIN-01"]


def test_user_serial_override_appends_without_changing_previous_canonical_row():
    run = SimpleNamespace(order=ProjectOrder(1, "50577574", "DAP", None, category="Flow"), run_id=7)
    cable_line = FlowDclLine("50577574", "ACME", "dcl.pdf", 2, None, "CFL00335", "Cable", Decimal("300"), "Each")
    cable = FlowReviewItem(
        cable_line, "UHF cable", "{SO}-CFL00335-{Q}m",
        ["50577574-CFL00335-400m"], Decimal("300"), ReviewStatus.APPROVED,
        catalogue_key="CFL00335", approved=True,
    )
    live = LiveSheetRow(99, {
        "Forecast Month": "21-Oct", "Type": "Flow", "SO No.": "50577574",
        "Customer": "ACME", "Incoterms": "DAP", "Serial No": "50577574-CFL00335-300m",
    })
    result = FlowFinalPlanner().build(run, [(12, cable)], [live], date(2026, 10, 21))
    assert result.plan_rows[0].action.value == "create"
    assert result.plan_rows[0].existing_row_id is None


def test_flow_smartsheet_preview_overrides_are_used_in_final_plan():
    run = SimpleNamespace(order=ProjectOrder(1, "50577574", "DAP", None, category="Flow"), run_id=7)
    approved = item()
    approved.proposed_value_overrides = {"0": {
        "Serial No": "50577574-WIN-CUSTOM",
        "Customer": "Edited Customer",
        "Colour In/Ext": "7035/7035",
    }}

    result = FlowFinalPlanner().build(run, [(12, approved)], [], date(2026, 10, 21))

    assert result.plan_rows[0].serial == "50577574-WIN-CUSTOM"
    assert result.plan_rows[0].proposed_values["Serial No"] == "50577574-WIN-CUSTOM"
    assert result.plan_rows[0].proposed_values["Customer"] == "Edited Customer"
    assert result.plan_rows[0].proposed_values["Colour In/Ext"] == "7035/7035"
