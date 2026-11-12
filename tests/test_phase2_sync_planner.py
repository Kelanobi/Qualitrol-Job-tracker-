from datetime import date

import pytest

from app.phase2.models import EquipmentCandidate, LiveSheetRow, PlanAction, ProjectOrder
from app.phase2.sync_planner import APPROVED_FIELDS, SyncPlanner, enforce_write_allowlist


ORDER = ProjectOrder(row=2, so="50577444", term="CIP", workbook_customer="Technical Co")


def test_planner_uses_processing_date_commercial_customer_and_allowlist():
    candidate = EquipmentCandidate("OCU", serial="50577444-OCU-01", colour="9016/9016")
    rows = SyncPlanner().plan(ORDER, [candidate], [], "Commercial Co", date(2026, 10, 20))

    row = rows[0]
    assert row.action == PlanAction.CREATE
    assert row.proposed_values == {
        "Forecast Month": "20-Oct",
        "Type": "Project",
        "SO No.": "50577444",
        "Customer": "Commercial Co",
        "Incoterms": "CIP",
        "Serial No": "50577444-OCU-01",
        "Colour In/Ext": "9016/9016",
    }
    assert set(row.proposed_values) <= APPROVED_FIELDS
    assert "Technical Co" not in row.proposed_values.values()


def test_formula_and_plinth_columns_are_rejected_centrally():
    for field in ("Product", "Cell", "Plinth Colour", "Order Status"):
        with pytest.raises(ValueError, match="unapproved"):
            enforce_write_allowlist({field: "never"})


def test_components_do_not_inherit_cabinet_colour():
    candidate = EquipmentCandidate("PCU", serial="50577444-PCU-01", colour="7038/7038")
    row = SyncPlanner().plan(ORDER, [candidate], [], "Commercial Co", date(2026, 10, 20))[0]
    assert "Colour In/Ext" not in row.proposed_values


def test_duplicate_live_matches_and_missing_non_ocu_serial_are_blocked():
    duplicate = [
        LiveSheetRow(1, {"SO No.": "50577444", "Serial No": "50577444-RCC"}),
        LiveSheetRow(2, {"SO No.": "50577444", "Serial No": "50577444-RCC"}),
    ]
    rows = SyncPlanner().plan(
        ORDER,
        [EquipmentCandidate("RCC", serial="50577444-RCC"), EquipmentCandidate("GDDC")],
        duplicate,
        "Commercial Co",
        date(2026, 10, 20),
    )
    assert rows[0].action == PlanAction.BLOCKED
    assert any(issue.code == "DUPLICATE_LIVE_ROWS" for issue in rows[0].issues)
    assert rows[1].action == PlanAction.BLOCKED
    assert any(issue.code == "MISSING_SERIAL" for issue in rows[1].issues)


def test_existing_match_is_never_overridden():
    candidate = EquipmentCandidate("RCC", serial="50577444-RCC", colour="7038/7038")
    existing = LiveSheetRow(10, {
        "Forecast Month": "19-Oct", "Type": "Project", "SO No.": "50577444",
        "Customer": "Commercial Co", "Incoterms": "CIP", "Serial No": "50577444-RCC",
        "Colour In/Ext": "7038/7038", "Product": "formula value",
    })
    row = SyncPlanner().plan(ORDER, [candidate], [existing], "Commercial Co", date(2026, 10, 20))[0]
    assert row.action == PlanAction.UNCHANGED
    assert row.changes == {"Forecast Month": ("19-Oct", "20-Oct")}
