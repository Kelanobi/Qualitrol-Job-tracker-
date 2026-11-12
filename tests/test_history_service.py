from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Order, VerificationResult
from app.services.history_service import HistoryService


def make_result(number="100", status="Yes", mode="Manual"):
    item = VerificationResult(Order(2, "Project", "DMS", number), mode, datetime.now())
    item.specs = item.qa = item.dcl = item.drawings = item.fat = status
    item.completed_at = datetime.now()
    item.excel_update_status = "Saved"
    return item


def test_attempts_persist_across_service_instances(tmp_path):
    path = tmp_path / "history.db"
    HistoryService(path).record(make_result())
    assert len(HistoryService(path).latest()) == 1


def test_recheck_creates_new_attempt_and_preserves_old(tmp_path):
    service = HistoryService(tmp_path / "history.db")
    service.record(make_result(status="Missing"))
    service.record(make_result(status="Yes", mode="Recheck"))
    attempts = service.attempts_for_order("100")
    assert len(attempts) == 2
    assert attempts[0]["search_mode"] == "Recheck"


def test_latest_returns_one_row_per_order(tmp_path):
    service = HistoryService(tmp_path / "history.db")
    service.record(make_result("100", "Missing"))
    service.record(make_result("100", "Yes"))
    service.record(make_result("200", "Yes"))
    rows = service.latest()
    assert len(rows) == 2
    assert {row["order_number"] for row in rows} == {"100", "200"}


def test_searched_numbers_include_failed_attempts(tmp_path):
    service = HistoryService(tmp_path / "history.db")
    item = make_result("300")
    item.errors.append("network")
    item.excel_update_status = "Failed"
    service.record(item)
    assert service.searched_order_numbers() == {"300"}


def test_history_filter_and_search(tmp_path):
    service = HistoryService(tmp_path / "history.db")
    service.record(make_result("100", "Yes"))
    service.record(make_result("200", "Missing"))
    assert [row["order_number"] for row in service.latest(status="Fully Verified", query="10")] == ["100"]


def test_document_presence_filter_and_effective_status_ignore_technical_error(tmp_path):
    service = HistoryService(tmp_path / "history.db")
    complete = make_result("100", "Yes")
    complete.errors.append("Excel workbook was locked")
    complete.error_category = "Excel update error"
    service.record(complete)
    service.record(make_result("200", "Missing"))

    assert [row["order_number"] for row in service.latest(status="Completed Pass")] == ["100"]
    assert [row["order_number"] for row in service.latest(document="qa", presence="missing")] == ["200"]


def test_history_filters_by_project_or_flow(tmp_path):
    service = HistoryService(tmp_path / "history.db")
    project = make_result("100", "Yes")
    flow = make_result("200", "Yes")
    flow.order = Order(3, "Flow", "DMS", "200")
    flow.drawings = flow.fat = "N/A"
    service.record(project)
    service.record(flow)
    assert [row["order_number"] for row in service.latest(order_category="Project")] == ["100"]
    assert [row["order_number"] for row in service.latest(order_category="Flow")] == ["200"]


def test_excel_modification_history_preserves_every_employee_event(tmp_path, auth_session):
    service = HistoryService(tmp_path / "history.db")
    service.record_excel_modification(
        auth_session, "100", 2, [11], {11: "Missing", 15: None},
        {11: "Yes", 15: auth_session.email}, True,
    )
    later = type(auth_session)(**{**vars(auth_session), "email": "later@qualitrolcorp.com", "session_id": "later"})
    service.record_excel_modification(
        later, "100", 2, [11], {11: "Yes", 15: auth_session.email},
        {11: "Missing", 15: later.email}, True,
    )
    with service._connect() as connection:
        rows = list(connection.execute(
            "SELECT * FROM excel_modification_events WHERE order_number='100' ORDER BY id"
        ))
    assert [row["employee_email"] for row in rows] == [auth_session.email, later.email]
    assert len(rows) == 2


def test_existing_attempt_schema_is_migrated_without_losing_history(tmp_path):
    import sqlite3
    path = tmp_path / "history.db"
    connection = sqlite3.connect(path)
    connection.execute("""
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_number TEXT NOT NULL,
            order_category TEXT NOT NULL, search_mode TEXT NOT NULL,
            started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
            specs TEXT NOT NULL, qa TEXT NOT NULL, dcl TEXT NOT NULL,
            drawings TEXT NOT NULL, overall_status TEXT NOT NULL,
            excel_update_status TEXT NOT NULL, error_category TEXT,
            error_message TEXT, paths_json TEXT NOT NULL DEFAULT '{}'
        )
    """)
    connection.execute("""
        INSERT INTO attempts (
            order_number, order_category, search_mode, started_at, completed_at,
            specs, qa, dcl, drawings, overall_status, excel_update_status, paths_json
        ) VALUES ('old', 'Project', 'Manual', '2026-01-01', '2026-01-01',
                  'Yes', 'Yes', 'Yes', 'Yes', 'Fully Verified', 'Saved', '{}')
    """)
    connection.commit(); connection.close()
    service = HistoryService(path)
    assert service.latest()[0]["order_number"] == "old"
    with service._connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(attempts)")}
    assert {"employee_email", "session_id", "fat"} <= columns

