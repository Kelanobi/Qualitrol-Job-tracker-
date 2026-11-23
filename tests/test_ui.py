from __future__ import annotations

import os
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QComboBox, QLineEdit, QMessageBox, QPushButton, QTableWidgetItem,
)

from app.ui.dashboard import DashboardPage
from app.ui.history_page import HistoryPage
from app.ui.search_pages import AutomaticSearchPage, ManualSearchPage, ProgressPage
from app.ui.phase2_page import Phase2ProjectPage
from app.ui.login_page import LoginPage
from app.ui.main_window import MainWindow
from app.ui.results_page import ResultsPage
from app.phase2.models import (
    EquipmentCandidate, IssueSeverity, Phase2DryRun, PlanAction, ProjectOrder,
    SyncPlanRow, ValidationIssue,
)
from app.flow.models import FlowDclLine, FlowReviewItem, ReviewStatus
from app.flow.processor import FlowRun


def application():
    return QApplication.instance() or QApplication([])


def test_login_page_masks_password_and_enter_submits(tmp_path):
    application()
    session = object()
    class Controller:
        def authenticate(self, email, password):
            assert email == "kelvin.obi@qualitrolcorp.com"
            assert password == "Password123"
            return session
    page = LoginPage(Controller(), tmp_path)
    emitted = []
    page.authenticated.connect(emitted.append)
    assert page.password.echoMode() == QLineEdit.Password
    page.email.setText("kelvin.obi@qualitrolcorp.com")
    page.password.setText("Password123")
    page.password.returnPressed.emit()
    assert emitted == [session]
    assert page.password.text() == ""


def test_login_page_prefills_windows_username_and_remembered_password_masked(tmp_path):
    application()
    class Controller:
        def login_defaults(self):
            return "kelvin.obi", "RememberedPassword"

        def sign_out(self):
            pass

    page = LoginPage(Controller(), tmp_path)
    page.reset()

    assert page.email.text() == "kelvin.obi"
    assert page.password.text() == "RememberedPassword"
    assert page.password.echoMode() == QLineEdit.Password


def test_history_apply_filters_emits_current_values():
    application()
    page = HistoryPage()
    emitted = []
    page.refresh_requested.connect(emitted.append)
    page.search.setText("505")
    page.status.setCurrentText("Missing Documents")
    page.order_type.setCurrentText("Project")
    page._refresh()
    assert emitted[0]["query"] == "505"
    assert emitted[0]["status"] == "Missing Documents"
    assert emitted[0]["order_type"] == "Project"


def test_history_empty_filter_feedback_is_visible():
    application()
    page = HistoryPage()
    page.search.setText("999")
    page.set_rows([])
    assert page.filter_message.text() == "No history records match the applied filters."


def test_dashboard_has_explicit_reset_month_button():
    application()
    page = DashboardPage()
    assert any(button.text() == "Reset Month" for button in page.findChildren(QPushButton))


def test_dashboard_has_no_filter_dropdowns_and_history_keeps_date_filter():
    application()
    dashboard = DashboardPage()
    history = HistoryPage()
    assert dashboard.findChildren(QComboBox) == []
    assert history.period.findData("current_month") >= 0
    assert all(combo.currentText() not in {"Drawings", "Has document"} for combo in history.findChildren(QComboBox))


def test_dashboard_offers_automatic_recheck_all():
    application()
    page = DashboardPage()
    emitted = []
    page.automatic_recheck_requested.connect(lambda: emitted.append(True))
    button = next(
        button for button in page.findChildren(QPushButton)
        if "Recheck All Orders" in button.text()
    )
    button.click()
    assert emitted == [True]


def test_dashboard_stacks_actions_on_narrow_logical_screen():
    app = application()
    page = DashboardPage()
    page.resize(900, 700)
    page.show()
    app.processEvents()
    assert page.actions_layout.direction() == QBoxLayout.TopToBottom

    page.resize(1400, 900)
    app.processEvents()
    assert page.actions_layout.direction() == QBoxLayout.LeftToRight
    page.close()


def test_main_window_dashboard_uses_narrow_scroll_viewport(tmp_path):
    app = application()
    class Controller:
        def login_defaults(self):
            return "kelvin.obi", "RememberedPassword"

        def sign_out(self):
            pass

    window = MainWindow(Controller(), tmp_path)
    window.shell.setCurrentWidget(window.application_root)
    window.resize(900, 650)
    window.show()
    app.processEvents()

    assert window.stack.width() <= window.content_scroll.viewport().width() + 2
    assert window.dashboard.actions_layout.direction() == QBoxLayout.TopToBottom
    window.close()


def test_phase2_has_shared_add_prefix_action():
    application()
    page = Phase2ProjectPage()
    emitted = []
    page.prefix_add_requested.connect(lambda: emitted.append(True))
    page.add_prefix_button.click()
    assert emitted == [True]


def test_dashboard_uses_percentage_summary_and_last_checked_column():
    application()
    page = DashboardPage()
    page.set_data({
        "total": 10,
        "completed": 3,
        "missing": 7,
        "recent": [{
            "order_number": "50538114", "order_category": "Project", "specs": "Yes",
            "qa": "Missing", "dcl": "Yes", "drawings": "Yes",
            "fat": "Yes",
            "overall_status": "Missing Documents", "completed_at": "2026-10-22T14:28:00",
        }],
    }, "22 Oct 2026  14:30")
    assert page.verified_metric.percent_label.text() == "30%"
    assert page.missing_metric.percent_label.text() == "70%"
    assert page.table.horizontalHeaderItem(8).text() == "Last Checked"
    assert page.table.item(0, 8).text() == "2026-10-22 14:28:00"


def test_manual_quantity_spinner_arrows_change_field_count():
    app = application()
    page = ManualSearchPage()
    page.show()
    app.processEvents()
    spinner = page.quantity
    QTest.mouseClick(page.quantity_up, Qt.LeftButton)
    app.processEvents()
    assert spinner.value() == 2
    assert len(page.fields) == 2
    QTest.mouseClick(page.quantity_down, Qt.LeftButton)
    app.processEvents()
    assert spinner.value() == 1
    assert len(page.fields) == 1


def test_automatic_search_displays_busy_state_until_result():
    application()
    page = AutomaticSearchPage()
    page.set_searching()
    assert page.activity.isHidden() is False
    assert page.start_button.isEnabled() is False
    assert "Scanning" in page.count_label.text()
    page.set_result("No new DMS orders found.")
    assert page.activity.isHidden() is True
    assert page.start_button.isEnabled() is True
    assert page.count_label.text() == "No new DMS orders found."


def test_verification_progress_displays_percentage():
    application()
    page = ProgressPage()
    page.update_progress(3, 4, "50538114", "Checking DCL")
    assert page.progress.format() == "75%"
    assert "75% complete" in page.count_label.text()


def test_manual_validation_displays_animated_progress_and_immediate_cancel_feedback():
    application()
    page = ProgressPage()
    emitted = []
    page.cancel_requested.connect(lambda: emitted.append(True))
    page.start_indeterminate("Validating order numbers", "Reading workbook…")
    assert page.progress.minimum() == 0
    assert page.progress.maximum() == 0
    assert page.order_label.text() == "Validating order numbers"
    page.cancel_button.click()
    assert emitted == [True]
    assert page.cancel_button.text() == "Cancelling safely…"
    assert page.cancel_button.isEnabled() is False


def test_phase2_progress_displays_percentage():
    application()
    page = Phase2ProjectPage()
    page.set_busy(True, "Starting")
    page.set_progress(64, "Reconciling drawings…")
    assert page.activity.value() == 64
    assert page.activity.format() == "64%"
    assert page.summary.text() == "Reconciling drawings…"


def test_phase2_page_exposes_preview_and_confirmation_only_after_online_plan():
    application()
    page = Phase2ProjectPage()
    run = Phase2DryRun(
        order=ProjectOrder(2, "50577574", "CIP", None), documents=[], issues=[],
        equipment=[EquipmentCandidate("RCC", serial="50577574-RCC")],
        plan_rows=[SyncPlanRow(
            PlanAction.CREATE, "RCC", "50577574-RCC", None,
            {"SO No.": "50577574", "Serial No": "50577574-RCC"}, {},
        )],
    )
    page.set_run(run, online=False)
    assert page.sync_button.isEnabled() is False
    page.set_run(run, online=True)
    assert page.sync_button.isEnabled() is True
    assert page.preview.rowCount() == 1
    assert page.tabs.currentWidget() is page.preview


def test_online_preview_sync_control_always_responds_even_when_rows_are_unchanged():
    application()
    page = Phase2ProjectPage()
    run = Phase2DryRun(
        order=ProjectOrder(2, "50601087", "DDP", "Customer"), documents=[], issues=[],
        equipment=[EquipmentCandidate("HVI", serial="50601087-HVI-01")],
        plan_rows=[SyncPlanRow(
            PlanAction.UNCHANGED, "HVI", "50601087-HVI-01", 42,
            {"SO No.": "50601087", "Serial No": "50601087-HVI-01"}, {},
        )],
    )
    emitted = []
    page.sync_requested.connect(emitted.append)

    page.set_run(run, online=True)
    page.sync_button.click()

    assert page.sync_button.isEnabled() is True
    assert emitted == [run]
    assert "Opening synchronization confirmation" in page.summary.text()


def test_project_smartsheet_preview_is_editable_and_updates_sync_payload():
    application()
    page = Phase2ProjectPage()
    run = Phase2DryRun(
        order=ProjectOrder(2, "50577574", "CIP", "Customer"), documents=[], issues=[],
        equipment=[EquipmentCandidate("RCC", serial="50577574-RCC-01")],
        plan_rows=[SyncPlanRow(
            PlanAction.CREATE, "RCC", "50577574-RCC-01", None,
            {"SO No.": "50577574", "Serial No": "50577574-RCC-01", "Customer": "Customer"}, {},
        )],
    )
    page.set_run(run, online=True)
    serial_column = 2 + page.preview_fields.index("Serial No")
    page.preview.item(0, serial_column).setText("50577574-RCC-99")

    assert run.plan_rows[0].serial == "50577574-RCC-99"
    assert run.plan_rows[0].proposed_values["Serial No"] == "50577574-RCC-99"


def test_project_blocked_row_is_unblocked_when_missing_serial_is_edited():
    application()
    page = Phase2ProjectPage()
    run = Phase2DryRun(
        order=ProjectOrder(2, "50577574", "CIP", "Customer"), documents=[], issues=[], equipment=[],
        plan_rows=[SyncPlanRow(
            PlanAction.BLOCKED, "GDDC", None, None, {"SO No.": "50577574"}, {},
            [ValidationIssue("MISSING_SERIAL", IssueSeverity.BLOCKING, "Serial is required.")],
        )],
    )
    page.set_run(run, online=True)
    serial_column = 2 + page.preview_fields.index("Serial No")
    page.preview.item(0, serial_column).setText("50577574-GDDC-01")

    assert run.plan_rows[0].action == PlanAction.CREATE
    assert run.plan_rows[0].writable is True


def test_project_blocking_discrepancy_can_be_explicitly_overridden(monkeypatch):
    application()
    page = Phase2ProjectPage()
    run = Phase2DryRun(
        order=ProjectOrder(2, "50591150", "EXW", "Customer"), documents=[], issues=[], equipment=[],
        plan_rows=[SyncPlanRow(
            PlanAction.BLOCKED, "RCC", "50591150-CAB-RCC-01", None,
            {
                "SO No.": "50591150", "Serial No": "50591150-CAB-RCC-01",
                "Colour In/Ext": "7035/7035",
            }, {},
            [ValidationIssue(
                "DCA_DRAWING_COLOUR_CONFLICT", IssueSeverity.BLOCKING,
                "DCA finish 7032/7032 conflicts with drawing finish 7035/7035.",
            )],
        )],
    )
    page.set_run(run, online=True)
    page.preview.selectRow(0)
    monkeypatch.setattr(
        "app.ui.phase2_page.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )

    page.override_project_button.click()

    assert run.plan_rows[0].action == PlanAction.CREATE
    assert run.plan_rows[0].writable is True
    assert any(issue.code == "USER_OVERRIDE_APPROVED" for issue in run.plan_rows[0].issues)
    assert not any(issue.severity == IssueSeverity.BLOCKING for issue in run.plan_rows[0].issues)


def test_results_passes_single_order_directly_to_phase2():
    application()
    page = ResultsPage()
    result = type("Result", (), {
        "order": type("Order", (), {"number": "50605782", "category": "Project"})(),
        "specs": "Missing", "qa": "Missing", "dcl": "Yes", "drawings": "Missing", "fat": "Missing",
        "overall_status": "Missing Documents", "excel_update_status": "Saved", "errors": [],
    })()
    page.set_results([result])
    emitted = []
    page.phase2_requested.connect(emitted.append)
    page._proceed_to_phase2()
    assert emitted == [["50605782"]]


def test_phase2_batch_results_can_be_switched_from_order_selector():
    application()
    page = Phase2ProjectPage()
    runs = []
    for so in ("50538114", "50577444"):
        runs.append((Phase2DryRun(
            order=ProjectOrder(2, so, "CIP", "Customer"), documents=[], issues=[], equipment=[],
            plan_rows=[SyncPlanRow(
                PlanAction.CREATE, "RCC", f"{so}-RCC", None,
                {"SO No.": so, "Serial No": f"{so}-RCC"}, {},
            )],
        ), True))
    page.set_batch_runs(runs)
    assert page.current_run.order.so == "50538114"
    assert page.sync_button.text() == "Apply Current Job"
    assert page.sync_all_button.isHidden() is False
    assert page.sync_all_button.isEnabled() is True
    page.order.setCurrentText("50577444")
    assert page.current_run.order.so == "50577444"


def test_final_flow_preview_replaces_review_run_in_batch():
    application()
    page = Phase2ProjectPage()
    order = ProjectOrder(2, "50594733", "CIP", "Customer", category="Flow")
    review_run = FlowRun(order, [], [], [], [])
    page.set_batch_runs([(review_run, False), (Phase2DryRun(
        order=ProjectOrder(3, "50538114", "CIP", "Customer"), documents=[], issues=[], equipment=[],
        plan_rows=[],
    ), True)])
    final = Phase2DryRun(
        order=order, documents=[], issues=[], equipment=[],
        plan_rows=[SyncPlanRow(
            PlanAction.CREATE, "AE07263", "50594733-AE07263-01", None,
            {"SO No.": "50594733", "Serial No": "50594733-AE07263-01"}, {},
        )],
    )
    page.replace_batch_run(final, True)
    emitted = []
    page.sync_all_requested.connect(emitted.append)
    page.sync_all_button.click()
    assert [run.order.so for run in emitted[0]] == ["50594733", "50538114"]


def test_flow_job_opens_on_smartsheet_preview_when_selected():
    application()
    page = Phase2ProjectPage()
    order = ProjectOrder(2, "50602018", "CIP", "Customer", category="Flow")
    line = FlowDclLine(
        order.so, "Customer", "DCL.pdf", 1, "2.1", "PART", "Part",
        Decimal("105"), "Each",
    )
    item = FlowReviewItem(
        line, "PART", "{SO}-PART-{Q}", ["50602018-PART-105"],
        Decimal("105"), ReviewStatus.NEEDS_REVIEW,
    )
    review_run = FlowRun(order, [], [line], [item], [])

    page.set_batch_runs([(review_run, False)])

    assert page.tabs.currentWidget() is page.preview


def test_flow_smartsheet_preview_serial_is_editable_and_emits_persisted_location():
    application()
    page = Phase2ProjectPage()
    order = ProjectOrder(2, "50602018", "CIP", "Customer", category="Flow")
    line = FlowDclLine(order.so, "Customer", "DCL.pdf", 1, "2.1", "PART", "Part", Decimal("105"), "Each")
    item = FlowReviewItem(
        line, "PART", "{SO}-PART-{Q}", ["50602018-PART-105"],
        Decimal("105"), ReviewStatus.NEEDS_REVIEW,
    )
    run = FlowRun(order, [], [line], [item], [])
    run.review_records = [(77, item)]
    emitted = []
    page.flow_preview_edit_requested.connect(lambda *values: emitted.append(values))
    page.set_flow_run(run)

    serial_cell = page.preview.item(0, 7)
    assert bool(serial_cell.flags() & Qt.ItemIsEditable)
    serial_cell.setText("50602018-PART-CORRECTED")

    assert emitted == [(77, 0, "Serial No", "50602018-PART-CORRECTED")]


def test_flow_approve_defaults_to_all_visible_rows_when_selection_is_empty():
    application()
    page = Phase2ProjectPage()
    page.flow_review.setRowCount(2)
    page.flow_review.setItem(0, 0, QTableWidgetItem("41"))
    page.flow_review.setItem(1, 0, QTableWidgetItem("42"))
    page.flow_review.clearSelection()
    assert page.selected_flow_ids() == {41, 42}


def test_recognised_flow_defaults_to_project_style_serial_preview():
    application()
    page = Phase2ProjectPage()
    order = ProjectOrder(2, "50594733", "FCA", "Workbook customer", category="Flow")
    line = FlowDclLine(
        so="50594733", customer="Siemens Energy LLC", source_file="50594733-DCL.pdf",
        page=1, line_reference="2.1", part_code="AE07263",
        description="Portable PDM System", quantity=Decimal("2"), unit="Each",
    )
    item = FlowReviewItem(
        line=line, category="AE07263", serial_pattern="{SO}-AE07263-{NN}",
        proposed_serials=["50594733-AE07263-01", "50594733-AE07263-02"],
        represented_quantity=Decimal("2"), status=ReviewStatus.RECOGNISED,
    )
    run = FlowRun(order, [], [line], [item], [])

    page.set_flow_run(run)

    assert page.tabs.currentWidget() is page.preview
    assert page.preview.rowCount() == 2
    assert page.preview.item(0, 7).text() == "50594733-AE07263-01"
    assert page.preview.item(1, 7).text() == "50594733-AE07263-02"
    assert page.preview.item(0, 3).text() == "Flow"

