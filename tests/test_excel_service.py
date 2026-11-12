from __future__ import annotations

from datetime import datetime
import os

import pytest
from openpyxl import load_workbook
from openpyxl.styles import PatternFill

import app.services.excel_service as excel_service_module
from app.models import Order, VerificationResult
from app.services.excel_service import (
    ExcelService, MissingAuthenticatedSessionError, WorkbookConflictError, WorkbookError,
)


def result(order, values=("Yes", "Yes", "Yes", "N/A", "N/A")):
    item = VerificationResult(order, "Manual", datetime.now())
    item.specs, item.qa, item.dcl, item.drawings = values[:4]
    item.fat = values[4] if len(values) > 4 else "N/A"
    item.completed_at = datetime.now()
    return item


def test_reads_only_dms_and_preserves_leading_zero(workbook_path):
    orders = ExcelService(workbook_path).read_orders()
    assert [item.number for item in orders] == ["00123", "456"]


def test_repeated_read_reuses_unchanged_workbook_cache(workbook_path, monkeypatch):
    calls = []
    original = excel_service_module.load_workbook
    monkeypatch.setattr(
        excel_service_module,
        "load_workbook",
        lambda *args, **kwargs: (calls.append(args[0]) or original(*args, **kwargs)),
    )
    service = ExcelService(workbook_path)
    first = service.read_orders()
    second = service.read_orders()
    found, errors = service.find_orders(["456", "789"])
    assert len(calls) == 1
    assert [order.number for order in first] == [order.number for order in second]
    assert first is not second
    assert [order.number for order in found] == ["456"]
    assert "IP value stream" in errors["789"]


def test_only_k_to_n_of_matching_dms_row_are_modified(workbook_path, auth_session):
    service = ExcelService(workbook_path, session_provider=lambda: auth_session)
    service.update_results([result(service.read_orders()[0])])
    workbook = load_workbook(workbook_path, data_only=False)
    sheet = workbook["Sheet1"]
    assert sheet["O1"].value == "FAT"
    assert sheet["P1"].value == "Modified by"
    assert [sheet.cell(2, column).value for column in range(11, 16)] == ["Yes", "Yes", "Yes", "N/A", "N/A"]
    assert sheet["D2"].value == "keep"
    assert sheet["P2"].value == auth_session.email
    assert sheet["P2"].alignment.shrink_to_fit is True
    assert sheet["P3"].value == "unchanged"
    assert sheet["Q2"].value == "=1+1"
    assert [sheet.cell(4, column).value for column in range(11, 15)] == ["IP"] * 4
    workbook.close()


def test_error_values_preserve_existing_cells(workbook_path, auth_session):
    service = ExcelService(workbook_path, session_provider=lambda: auth_session)
    order = service.read_orders()[1]
    service.update_results([result(order, ("Yes", "Error", "Missing", "Yes"))])
    workbook = load_workbook(workbook_path)
    sheet = workbook["Sheet1"]
    assert [sheet.cell(3, column).value for column in range(11, 15)] == ["Yes", "Old", "Missing", "Yes"]
    workbook.close()


def test_unknown_manual_order_is_rejected(workbook_path):
    found, errors = ExcelService(workbook_path).find_orders(["999"])
    assert found == []
    assert "not found" in errors["999"]


def test_ip_order_is_rejected_with_specific_message(workbook_path):
    found, errors = ExcelService(workbook_path).find_orders(["789"])
    assert found == []
    assert "IP value stream" in errors["789"]


def test_missing_workbook_is_actionable(tmp_path):
    with pytest.raises(WorkbookError, match="could not be found"):
        ExcelService(tmp_path / "missing.xlsx").read_orders()


def test_concurrent_change_is_detected(workbook_path, monkeypatch, auth_session):
    service = ExcelService(workbook_path, session_provider=lambda: auth_session)
    order = service.read_orders()[0]
    snapshots = [service.snapshot(), type(service.snapshot())(999, 999)]
    monkeypatch.setattr(service, "snapshot", lambda: snapshots.pop(0))
    with pytest.raises(WorkbookConflictError):
        service.update_results([result(order)])


def test_unchanged_row_preserves_previous_modified_by(workbook_path, auth_session):
    service = ExcelService(workbook_path, session_provider=lambda: auth_session)
    order = service.read_orders()[1]
    unchanged = result(order, ("Old", "Old", "Old", "Old", "Error"))
    service.update_results([unchanged])
    workbook = load_workbook(workbook_path)
    assert workbook["Sheet1"]["O3"].value == "unchanged"
    workbook.close()


def test_workbook_write_requires_authenticated_session(workbook_path):
    service = ExcelService(workbook_path)
    with pytest.raises(MissingAuthenticatedSessionError):
        service.update_results([result(service.read_orders()[0])])


def test_multiple_changed_rows_are_attributed_and_audited(workbook_path, auth_session):
    audits = []
    service = ExcelService(
        workbook_path, session_provider=lambda: auth_session,
        audit_recorder=lambda *args: audits.append(args),
    )
    orders = service.read_orders()
    service.update_results([result(orders[0]), result(orders[1])])
    workbook = load_workbook(workbook_path)
    assert workbook["Sheet1"]["P2"].value == auth_session.email
    assert workbook["Sheet1"]["P3"].value == auth_session.email
    workbook.close()
    assert len(audits) == 2
    assert all(event[-2] is True for event in audits)
    assert all(event[4] for event in audits)  # changed columns


def test_later_employee_replaces_modified_by_without_erasing_audit(workbook_path, auth_session):
    audits = []
    first = ExcelService(
        workbook_path, session_provider=lambda: auth_session,
        audit_recorder=lambda *args: audits.append(args),
    )
    first.update_results([result(first.read_orders()[0])])
    later = type(auth_session)(**{**vars(auth_session), "email": "another.user@qualitrolcorp.com", "session_id": "later"})
    second = ExcelService(
        workbook_path, session_provider=lambda: later,
        audit_recorder=lambda *args: audits.append(args),
    )
    order = second.read_orders()[0]
    second.update_results([result(order, ("Missing", "Yes", "Yes", "N/A"))])
    workbook = load_workbook(workbook_path)
    assert workbook["Sheet1"]["P2"].value == later.email
    workbook.close()
    assert [event[0].email for event in audits] == [auth_session.email, later.email]


def test_save_failure_is_audited_as_unsaved(workbook_path, auth_session, monkeypatch):
    audits = []
    service = ExcelService(
        workbook_path, session_provider=lambda: auth_session,
        audit_recorder=lambda *args: audits.append(args),
    )
    monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(WorkbookError, match="locked"):
        service.update_results([result(service.read_orders()[0])])
    assert len(audits) == 1
    assert audits[0][-2] is False
    assert "locked" in audits[0][-1]


def test_workbook_formatting_and_unrelated_formula_are_preserved(workbook_path, auth_session):
    workbook = load_workbook(workbook_path)
    workbook["Sheet1"]["D2"].fill = PatternFill(fill_type="solid", fgColor="00FF00")
    workbook.save(workbook_path); workbook.close()
    service = ExcelService(workbook_path, session_provider=lambda: auth_session)
    service.update_results([result(service.read_orders()[0])])
    workbook = load_workbook(workbook_path, data_only=False)
    assert workbook["Sheet1"]["D2"].fill.fgColor.rgb == "0000FF00"
    assert workbook["Sheet1"]["Q2"].value == "=1+1"
    workbook.close()


def test_manual_search_never_creates_rows(workbook_path):
    service = ExcelService(workbook_path)
    before = load_workbook(workbook_path)["Sheet1"].max_row
    service.find_orders(["999"])
    after = load_workbook(workbook_path)["Sheet1"].max_row
    assert before == after

