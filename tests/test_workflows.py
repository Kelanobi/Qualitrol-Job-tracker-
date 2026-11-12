from __future__ import annotations

from app.controller import ApplicationController


def controller(workbook_path, tmp_path):
    return ApplicationController(workbook_path, tmp_path / "docs", tmp_path / "history.db")


def test_automatic_search_excludes_previously_attempted(workbook_path, tmp_path):
    app = controller(workbook_path, tmp_path)
    order = app.excel.read_orders()[0]
    result = app.process_order(order, "Automatic")
    result.excel_update_status = "Failed"
    app.history.record(result)
    assert [item.number for item in app.automatic_candidates()] == ["456"]


def test_automatic_recheck_all_includes_previously_attempted_orders(workbook_path, tmp_path):
    app = controller(workbook_path, tmp_path)
    order = app.excel.read_orders()[0]
    app.history.record(app.process_order(order, "Automatic"))

    assert [item.number for item in app.all_orders()] == ["00123", "456"]


def test_manual_search_allows_previously_searched_order(workbook_path, tmp_path):
    app = controller(workbook_path, tmp_path)
    order = app.excel.read_orders()[0]
    app.history.record(app.process_order(order, "Manual"))
    found, errors = app.validate_manual_orders(["00123"])
    assert not errors
    assert found[0].number == "00123"


def test_manual_validation_skips_blanks_and_keeps_one_duplicate(workbook_path, tmp_path):
    app = controller(workbook_path, tmp_path)
    found, errors = app.validate_manual_orders(["", "456", "456"])
    assert [order.number for order in found] == ["456"]
    assert len(errors) == 1
    assert "included only once" in next(iter(errors.values()))


def test_manual_validation_requires_at_least_one_number(workbook_path, tmp_path):
    app = controller(workbook_path, tmp_path)
    found, errors = app.validate_manual_orders(["", "  "])
    assert not found
    assert errors == {"empty": "Enter at least one DMS order number."}


def test_dashboard_counts_are_not_hardcoded(workbook_path, tmp_path):
    app = controller(workbook_path, tmp_path)
    data = app.dashboard_data()
    assert data["total"] == 2
    assert data["completed"] == 0
    assert data["missing"] == 2
    assert data["completed"] + data["missing"] == data["total"]

