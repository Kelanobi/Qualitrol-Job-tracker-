from app.phase2.models import LiveSheetRow, PlanAction, SyncPlanRow
from app.phase2.sheet_layout import (
    Placement,
    SheetLayoutPlanner,
    assign_create_placements,
    is_safe_reserved_row,
)


def row(number, row_id, type_, so=None, serial=None, **extra):
    values = {"Type": type_, "SO No.": so, "Serial No": serial, **extra}
    return LiveSheetRow(row_id, values, row_number=number)


def test_project_reuses_96_then_inserts_4_before_first_flow():
    rows = [row(i, i, "Project") for i in range(222, 318)]
    rows += [row(318, 1000, "Flow", "500", "500-OCU-01")]
    plan = SheetLayoutPlanner().plan(rows, "Project", 100)
    assert [item.placement for item in plan].count(Placement.REUSE) == 96
    assert [item.placement for item in plan].count(Placement.INSERT_BEFORE_FLOW) == 4
    assert all(item.boundary_row_id == 1000 for item in plan[-4:])


def test_flow_reuses_10_then_appends_88_without_project_slots():
    rows = [row(i, i, "Project") for i in range(222, 318)]
    rows += [row(318, 1000, "Flow", "500", "500-X")]
    rows += [row(i, i + 1000, "Flow") for i in range(431, 441)]
    plan = SheetLayoutPlanner().plan(rows, "Flow", 98)
    assert [item.placement for item in plan].count(Placement.REUSE) == 10
    assert [item.placement for item in plan].count(Placement.APPEND_FLOW) == 88


def test_business_content_attachments_and_comments_make_blank_row_unsafe():
    assert not is_safe_reserved_row(row(1, 1, "Project", Customer="someone"))
    assert not is_safe_reserved_row(LiveSheetRow(2, {"Type": "Project"}, has_attachments=True))
    assert not is_safe_reserved_row(LiveSheetRow(3, {"Type": "Project"}, has_discussions=True))
    assert is_safe_reserved_row(row(4, 4, "Project", **{"Forecast Month": "21-Oct", "Product": "formula"}))


def test_smartsheet_system_values_do_not_disqualify_reserved_row_222():
    reserved = row(
        222, 222, "Project", **{
            "Forecast Month": "10-Oct", "Cell": "#NO MATCH", "Week": "#INVALID DATA TYPE",
            "Modified": "2026-10-07T06:58:09Z", "Modified By": "user@example.com",
        }
    )
    plan = SheetLayoutPlanner().plan([reserved], "Project", 1)
    assert plan == [plan[0]]
    assert plan[0].placement == Placement.REUSE
    assert plan[0].reserved_row_id == 222


def test_rows_before_stream_start_boundaries_are_not_reused():
    rows = [row(221, 1, "Project"), row(222, 2, "Project"), row(430, 3, "Flow"), row(431, 4, "Flow")]
    project = SheetLayoutPlanner().plan(rows, "Project", 1)
    flow = SheetLayoutPlanner().plan(rows, "Flow", 1)
    assert project[0].reserved_row_id == 2
    assert flow[0].reserved_row_id == 4


def test_stale_batch_reservation_is_reassigned_to_next_free_project_row():
    plan = SyncPlanRow(
        action=PlanAction.CREATE,
        equipment_type="RCC",
        serial="50538114-RCC-01",
        existing_row_id=None,
        proposed_values={"SO No.": "50538114", "Serial No": "50538114-RCC-01"},
        changes={},
    )
    initial = [row(222, 222, "Project"), row(223, 223, "Project")]
    assign_create_placements([plan], initial, "Project")
    assert plan.target_row_id == 222

    # Another order in the same batch has occupied the originally reserved row.
    refreshed = [
        row(222, 222, "Project", "50577444", "50577444-RDC/GDDC-01"),
        row(223, 223, "Project"),
    ]
    plan.placement = None
    plan.target_row_id = None
    plan.boundary_row_id = None
    assign_create_placements([plan], refreshed, "Project")

    assert plan.target_row_id == 223


def test_project_starts_immediately_after_last_populated_project():
    rows = [
        row(221, 1, "Project", "500", "500-OCU-01"),
        row(222, 2, "Project", **{"Modified by": "formula-user"}),
        row(223, 3, "Project", **{"Modified by": "formula-user"}),
        row(431, 4, "Flow", "600", "600-A-01"),
    ]
    plan = SheetLayoutPlanner().plan(rows, "Project", 2)
    assert [item.reserved_row_id for item in plan] == [2, 3]


def test_project_uses_blank_type_rows_immediately_after_project_block():
    rows = [
        row(221, 1, "Project", "50579182", "50579182-OCU-12"),
        row(222, 2, ""),
        row(223, 3, ""),
        row(246, 4, "Project"),
        row(431, 5, "Flow", "50605782", "50605782-CAB-01"),
    ]
    plan = SheetLayoutPlanner().plan(rows, "Project", 2)
    assert [item.reserved_row_id for item in plan] == [2, 3]


def test_flow_starts_immediately_after_last_populated_flow():
    rows = [
        row(495, 1, "Flow", "600", "600-A-01"),
        row(496, 2, "Flow", **{"Modified by": "formula-user"}),
        row(497, 3, "Flow", **{"Modified by": "formula-user"}),
    ]
    plan = SheetLayoutPlanner().plan(rows, "Flow", 2)
    assert [item.reserved_row_id for item in plan] == [2, 3]
