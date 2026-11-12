from dataclasses import dataclass

import pytest

from app.phase2.models import PlanAction, SyncPlanRow
from app.phase2.smartsheet_client import (
    OfficialSdkSyncGateway, ReadOnlySmartsheetClient, SmartsheetConfigurationError,
    missing_picklist_options, validate_plan_against_live_schema,
)
from app.phase2.sync_planner import APPROVED_FIELDS


@dataclass
class FakeReader:
    sheet: dict
    calls: int = 0

    def get_sheet(self, sheet_id):
        self.calls += 1
        return self.sheet


def make_sheet(remove=None, duplicate=None, locked=None, forecast_options=None):
    columns = []
    for index, title in enumerate(sorted(APPROVED_FIELDS), 1):
        if title == remove:
            continue
        columns.append({
            "id": index, "title": title,
            "type": "PICKLIST" if title == "Forecast Month" else "TEXT_NUMBER",
            "locked": title == locked,
            "options": forecast_options if title == "Forecast Month" else None,
        })
    if duplicate:
        columns.append({"id": 99, "title": duplicate, "type": "TEXT_NUMBER"})
    id_by_title = {column["title"]: column["id"] for column in columns}
    return {
        "columns": columns,
        "rows": [{"id": 42, "cells": [
            {"column_id": id_by_title["SO No."], "value": "50577444"},
            {"column_id": id_by_title["Serial No"], "value": "50577444-RCC"},
        ]}],
    }


def test_read_only_snapshot_resolves_live_column_ids_and_rows():
    reader = FakeReader(make_sheet())
    snapshot = ReadOnlySmartsheetClient("sheet-id", reader).snapshot()
    assert reader.calls == 1
    assert set(snapshot.columns) == APPROVED_FIELDS
    assert snapshot.rows[0].row_id == 42
    assert snapshot.rows[0].values["Serial No"] == "50577444-RCC"
    assert not hasattr(ReadOnlySmartsheetClient, "create_row")
    assert not hasattr(ReadOnlySmartsheetClient, "update_row")


@pytest.mark.parametrize("sheet, message", [
    (make_sheet(remove="Incoterms"), "missing approved columns"),
    (make_sheet(duplicate="SO No."), "duplicate approved column names"),
    (make_sheet(locked="Customer"), "locked or formula-driven"),
])
def test_unsafe_live_schema_is_rejected(sheet, message):
    with pytest.raises(SmartsheetConfigurationError, match=message):
        ReadOnlySmartsheetClient("sheet-id", FakeReader(sheet)).snapshot()


def test_missing_live_picklist_value_is_pending_without_blocking_the_row():
    snapshot = ReadOnlySmartsheetClient(
        "sheet-id", FakeReader(make_sheet(forecast_options=["10-Oct", "11-Nov"]))
    ).snapshot()
    rows = [SyncPlanRow(
        action=PlanAction.CREATE, equipment_type="RCC", serial="50577444-RCC",
        existing_row_id=None, proposed_values={"Forecast Month": "21-Oct"}, changes={},
    )]
    validate_plan_against_live_schema(rows, snapshot)
    assert rows[0].action == PlanAction.CREATE
    assert [issue.code for issue in rows[0].issues] == ["PICKLIST_OPTION_PENDING"]


def test_missing_dropdown_values_are_collected_once_for_automatic_creation():
    sheet = make_sheet(forecast_options=["21-Oct"])
    colour = next(column for column in sheet["columns"] if column["title"] == "Colour In/Ext")
    colour["type"] = "PICKLIST"
    colour["options"] = ["7035/7035"]
    snapshot = ReadOnlySmartsheetClient("sheet-id", FakeReader(sheet)).snapshot()
    rows = [
        SyncPlanRow(
            action=PlanAction.CREATE, equipment_type="RCC", serial=f"50536602-RCC-0{index}",
            existing_row_id=None,
            proposed_values={"Forecast Month": "25-Oct", "Colour In/Ext": "7032/9003"},
            changes={},
        )
        for index in (1, 2)
    ]

    assert missing_picklist_options(rows, snapshot) == {
        "Forecast Month": {"25-Oct"}, "Colour In/Ext": {"7032/9003"},
    }


def test_exact_live_picklist_value_remains_writable():
    snapshot = ReadOnlySmartsheetClient(
        "sheet-id", FakeReader(make_sheet(forecast_options=["21-Oct"]))
    ).snapshot()
    rows = [SyncPlanRow(
        action=PlanAction.CREATE, equipment_type="RCC", serial="50577444-RCC",
        existing_row_id=None, proposed_values={"Forecast Month": "21-Oct"}, changes={},
    )]
    validate_plan_against_live_schema(rows, snapshot)
    assert rows[0].action == PlanAction.CREATE
    assert rows[0].issues == []


def test_sync_gateway_reuses_one_sheet_snapshot_for_lookups_and_column_ids():
    reader = FakeReader(make_sheet())
    gateway = OfficialSdkSyncGateway.__new__(OfficialSdkSyncGateway)
    gateway.sheet_id = "sheet-id"
    gateway.reader = reader
    gateway._cached_snapshot = None
    gateway._rows_by_id = {}

    assert gateway.find_rows("50577444", "50577444-RCC")[0][0] == 42
    assert gateway.find_rows("50577444", "50577444-RCC")[0][0] == 42
    gateway._cells({"SO No.": "50577444", "Serial No": "50577444-RCC"})

    assert reader.calls == 1
