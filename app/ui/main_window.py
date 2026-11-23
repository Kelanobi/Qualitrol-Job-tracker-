from __future__ import annotations

import logging
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from app.controller import ApplicationController
from app.ui.dashboard import DashboardPage
from app.ui.history_page import HistoryPage
from app.ui.results_page import ResultsPage
from app.ui.phase2_page import Phase2ProjectPage
from app.ui.search_pages import AutomaticSearchPage, ManualSearchPage, ProgressPage
from app.ui.login_page import LoginPage
from app.workers.verification_worker import (
    AutomaticCandidateWorker, DashboardLoadWorker, ManualValidationWorker, VerificationWorker,
)
from app.workers.phase2_worker import Phase2PreviewWorker
from app.workers.phase2_batch_worker import Phase2BatchWorker
from app.workers.phase2_sync_worker import Phase2SyncWorker
from app.workers.phase2_batch_sync_worker import Phase2BatchSyncWorker
from app.workers.flow_finalize_worker import FlowFinalizeWorker
from app.workers.classification_worker import ClassificationCorrectionWorker


class MainWindow(QMainWindow):
    def __init__(self, controller: ApplicationController, assets_dir: Path):
        super().__init__()
        self.controller = controller
        self.assets_dir = Path(assets_dir)
        self.thread: QThread | None = None
        self.worker: VerificationWorker | None = None
        self.manual_validation_thread: QThread | None = None
        self.manual_validation_worker: ManualValidationWorker | None = None
        self.dashboard_thread: QThread | None = None
        self.dashboard_worker: DashboardLoadWorker | None = None
        self.candidate_thread: QThread | None = None
        self.candidate_worker: AutomaticCandidateWorker | None = None
        self._candidate_mode = "new"
        self.phase2_thread: QThread | None = None
        self.phase2_worker: Phase2PreviewWorker | None = None
        self.phase2_batch_thread: QThread | None = None
        self.phase2_batch_worker: Phase2BatchWorker | None = None
        self.phase2_sync_thread: QThread | None = None
        self.phase2_sync_worker: Phase2SyncWorker | None = None
        self._pending_phase2_so: str | None = None
        self.setWindowTitle("DMS Order Tracker")
        self.setWindowIcon(QIcon(str(self.assets_dir / "app_icon.ico")))
        self.resize(1360, 900)
        # Stay within the logical desktop area on smaller laptops and at
        # 125–175% Windows display scaling. Page scrolling preserves access to
        # every control when vertical space is limited.
        self.setMinimumSize(800, 560)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_header())
        self.stack = QStackedWidget()
        # The largest page must not impose its width on every other page. This
        # lets the dashboard use the actual viewport width at high DPI instead
        # of creating a permanently clipped 1080px-wide canvas.
        self.stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.stack.setMinimumWidth(0)
        self.content_scroll = QScrollArea()
        self.content_scroll.setObjectName("applicationScroll")
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.NoFrame)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.content_scroll.setWidget(self.stack)
        root_layout.addWidget(self.content_scroll, 1)
        self.application_root = root

        self.dashboard = DashboardPage(self.assets_dir)
        self.automatic = AutomaticSearchPage()
        self.manual = ManualSearchPage()
        self.progress = ProgressPage()
        self.history = HistoryPage()
        self.results = ResultsPage()
        self.phase2_page = Phase2ProjectPage()
        for page in (self.dashboard, self.automatic, self.manual, self.progress, self.history, self.results, self.phase2_page):
            self.stack.addWidget(page)

        self.dashboard.automatic_requested.connect(lambda: self.navigate("automatic"))
        self.dashboard.automatic_recheck_requested.connect(self.prepare_automatic_recheck_all)
        self.dashboard.manual_requested.connect(lambda: self.navigate("manual"))
        self.dashboard.history_requested.connect(self.show_history)
        self.dashboard.reset_requested.connect(self.reset_dashboard_month)
        self.dashboard.report_requested.connect(self.download_monthly_report)
        self.dashboard.detail_requested.connect(self.show_verification_detail)
        self.automatic.back_requested.connect(lambda: self.navigate("dashboard"))
        self.automatic.start_requested.connect(self.prepare_automatic)
        self.manual.back_requested.connect(lambda: self.navigate("dashboard"))
        self.manual.verify_requested.connect(self.prepare_manual)
        self.progress.cancel_requested.connect(self.cancel_current)
        self.history.refresh_requested.connect(self.refresh_history)
        self.history.recheck_requested.connect(self.prepare_recheck)
        self.history.back_requested.connect(lambda: self.navigate("dashboard"))
        self.history.detail_requested.connect(self.show_verification_detail)
        self.results.finish_requested.connect(lambda: self.navigate("dashboard"))
        self.results.history_requested.connect(lambda: self.show_history(""))
        self.results.phase2_requested.connect(self.prepare_phase2_batch)
        self.phase2_page.back_requested.connect(lambda: self.navigate("dashboard"))
        self.phase2_page.preview_requested.connect(self.prepare_phase2_preview)
        self.phase2_page.cancel_requested.connect(self.cancel_phase2)
        self.phase2_page.sync_requested.connect(self.confirm_phase2_sync)
        self.phase2_page.sync_all_requested.connect(self.confirm_phase2_batch_sync)
        self.phase2_page.flow_edit_requested.connect(self.edit_flow_review)
        self.phase2_page.flow_preview_edit_requested.connect(self.edit_flow_preview)
        self.phase2_page.flow_approve_requested.connect(self.approve_flow_review)
        self.phase2_page.flow_exclude_requested.connect(self.exclude_flow_review)
        self.phase2_page.flow_finalize_requested.connect(self.prepare_flow_final_preview)
        self.phase2_page.prefix_add_requested.connect(self.add_shared_flow_prefix)
        self.phase2_page.classification_change_requested.connect(self.confirm_change_to_flow)

        self.login_page = LoginPage(controller, self.assets_dir)
        self.login_page.authenticated.connect(self._authenticated)
        self.shell = QStackedWidget()
        self.shell.addWidget(self.login_page)
        self.shell.addWidget(self.application_root)
        self.setCentralWidget(self.shell)
        self.shell.setCurrentWidget(self.login_page)
        self.login_page.reset()

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(28, 15, 28, 14)
        logo = QLabel()
        pixmap = QIcon(str(self.assets_dir / "qualitrol_logo.png")).pixmap(320, 80)
        logo.setPixmap(pixmap)
        logo.setFixedSize(320, 78)
        logo.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(logo)
        divider = QFrame()
        divider.setFrameShape(QFrame.VLine)
        divider.setObjectName("headerDivider")
        layout.addWidget(divider)
        copy = QVBoxLayout()
        title = QLabel("DMS Order Tracker")
        title.setObjectName("appTitle")
        subtitle = QLabel("Document verification for DMS orders")
        subtitle.setObjectName("appSubtitle")
        copy.addWidget(title)
        copy.addWidget(subtitle)
        layout.addLayout(copy)
        layout.addStretch()

        self.menu_button = QToolButton()
        self.menu_button.setObjectName("hamburgerButton")
        self.menu_button.setText("☰")
        self.menu_button.setToolTip("Open menu")
        self.menu_button.setAccessibleName("Main menu")
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        main_menu = QMenu(self.menu_button)
        home_action = main_menu.addAction("Home")
        home_action.triggered.connect(lambda: self.navigate("dashboard"))
        main_menu.addSeparator()
        automatic_action = main_menu.addAction("Automatic Search")
        automatic_action.triggered.connect(lambda: self.navigate("automatic"))
        manual_action = main_menu.addAction("Manual Search")
        manual_action.triggered.connect(lambda: self.navigate("manual"))
        main_menu.addSeparator()
        history_action = main_menu.addAction("History")
        history_action.triggered.connect(lambda: self.show_history(""))
        main_menu.addSeparator()
        smartsheet_action = main_menu.addAction("Connect Smartsheet")
        smartsheet_action.triggered.connect(self.connect_smartsheet)
        main_menu.addSeparator()
        sign_out_action = main_menu.addAction("Sign Out")
        sign_out_action.triggered.connect(self.sign_out)
        self.menu_button.setMenu(main_menu)
        layout.addWidget(self.menu_button, 0, Qt.AlignVCenter)
        return header

    def _authenticated(self, session) -> None:
        self.dashboard.set_user(session.email)
        self.shell.setCurrentWidget(self.application_root)
        self.navigate("dashboard")
        if not self.controller.has_smartsheet_token():
            self.connect_smartsheet(first_time=True)

    def connect_smartsheet(self, first_time: bool = False) -> None:
        title = "Connect Smartsheet"
        prompt = (
            "This laptop is not connected to Smartsheet. Enter the API token to enable live previews and "
            "Confirm and Sync. The token is stored securely in Windows Credential Manager."
            if first_time else
            "Enter the Smartsheet API token. It will be validated and stored securely on this laptop."
        )
        token, accepted = QInputDialog.getText(
            self, title, prompt, QLineEdit.Password,
        )
        if not accepted or not token.strip():
            if first_time:
                QMessageBox.information(
                    self, "Offline mode",
                    "The app will continue in Offline preview mode. Use the menu and select Connect Smartsheet "
                    "when the token is available.",
                )
            return
        try:
            self.controller.configure_smartsheet_token(token)
        except Exception as exc:
            QMessageBox.critical(self, title, f"Smartsheet connection failed: {exc}")
            return
        QMessageBox.information(
            self, title,
            "Smartsheet is connected. Prepare the preview again to enable Confirm and Sync.",
        )

    def sign_out(self) -> None:
        self.cancel_current()
        self.cancel_phase2()
        self.controller.sign_out()
        self.login_page.reset()
        self.shell.setCurrentWidget(self.login_page)

    def closeEvent(self, event) -> None:
        try:
            # Manual pre-validation is read-only. If a network workbook call is
            # stalled, do not keep the whole application alive on window close.
            if self.manual_validation_thread and self.manual_validation_thread.isRunning():
                validation_thread = self.manual_validation_thread
                if self.manual_validation_worker:
                    self.manual_validation_worker.cancel()
                validation_thread.requestInterruption()
                validation_thread.terminate()
                validation_thread.wait(1000)
                self.manual_validation_worker = None
            self.cancel_current()
            self.cancel_phase2()
            self.controller.sign_out()
        finally:
            super().closeEvent(event)

    def navigate(self, name: str) -> None:
        pages = {"dashboard": self.dashboard, "automatic": self.automatic, "manual": self.manual, "history": self.history}
        if name == "phase2":
            self.show_phase2()
            return
        if name == "history":
            self.show_history("")
            return
        if name in pages:
            self.stack.setCurrentWidget(pages[name])
        if name == "dashboard":
            self.refresh_dashboard()

    def set_status(self, text: str, color: str) -> None:
        # Status remains part of application state without occupying header space.
        self._status_text = text
        self._status_color = color

    def refresh_dashboard(self) -> None:
        if self.dashboard_thread and self.dashboard_thread.isRunning():
            return
        self.dashboard.set_loading()
        self.set_status("Refreshing", "#ef8b13")
        self.dashboard_thread = QThread(self)
        self.dashboard_worker = DashboardLoadWorker(self.controller, "current_month", "", "")
        self.dashboard_worker.moveToThread(self.dashboard_thread)
        self.dashboard_thread.started.connect(self.dashboard_worker.run)
        self.dashboard_worker.loaded.connect(self._dashboard_loaded)
        self.dashboard_worker.failed.connect(self._dashboard_failed)
        self.dashboard_worker.loaded.connect(self.dashboard_thread.quit)
        self.dashboard_worker.failed.connect(self.dashboard_thread.quit)
        self.dashboard_thread.finished.connect(self.dashboard_worker.deleteLater)
        self.dashboard_thread.finished.connect(self.dashboard_thread.deleteLater)
        self.dashboard_thread.finished.connect(self._dashboard_thread_finished)
        self.dashboard_thread.start()

    def reset_dashboard_month(self) -> None:
        self.controller.reset_dashboard_month()
        self.refresh_dashboard()

    def download_monthly_report(self) -> None:
        try:
            path = self.controller.generate_monthly_report(previous=False)
        except Exception as exc:
            QMessageBox.critical(self, "Monthly report", str(exc))
            return
        QMessageBox.information(self, "Monthly report", f"Report saved to:\n{path}")

    def show_verification_detail(self, order_number: str) -> None:
        attempts = self.controller.history.attempts_for_order(order_number)
        if not attempts:
            return
        row = dict(attempts[0])
        status = self.controller.history.effective_status(row)
        paths = json.loads(row.get("paths_json") or "{}")
        lines = [
            f"Order: {row['order_number']}", f"Type: {row['order_category']}",
            f"Status: {status}", f"Checked: {str(row['completed_at']).replace('T', ' ')}",
            f"Checked by: {row.get('employee_email') or '—'}", "",
        ]
        for label, key in (("Specs", "specs"), ("QA", "qa"), ("DCL", "dcl"), ("Drawings", "drawings"), ("FAT", "fat")):
            path = paths.get(key, "")
            lines.append(f"{label}: {row.get(key)}" + (f"\n  {path}" if path else ""))
        if row.get("error_message"):
            lines.extend(["", "Technical diagnostic:", str(row["error_message"])])
        QMessageBox.information(self, "Verification details", "\n".join(lines))

    def _dashboard_loaded(self, data: dict) -> None:
        self.dashboard.set_data(data, datetime.now().strftime("%d %b %Y  %H:%M"))
        self.set_status("Ready", "#2eaa50")

    def _dashboard_failed(self, message: str) -> None:
        self.dashboard.set_unavailable(message)
        self.set_status("Error", "#d90416")

    def _dashboard_thread_finished(self) -> None:
        self.dashboard_thread = None
        self.dashboard_worker = None

    def prepare_automatic(self) -> None:
        if self.candidate_thread and self.candidate_thread.isRunning():
            return
        self.automatic.set_searching()
        self.set_status("Scanning", "#ef8b13")
        self.candidate_thread = QThread(self)
        self._candidate_mode = "new"
        self.candidate_worker = AutomaticCandidateWorker(self.controller)
        self.candidate_worker.moveToThread(self.candidate_thread)
        self.candidate_thread.started.connect(self.candidate_worker.run)
        self.candidate_worker.loaded.connect(self._automatic_candidates_loaded)
        self.candidate_worker.failed.connect(self._automatic_candidates_failed)
        self.candidate_worker.loaded.connect(self.candidate_thread.quit)
        self.candidate_worker.failed.connect(self.candidate_thread.quit)
        self.candidate_thread.finished.connect(self.candidate_worker.deleteLater)
        self.candidate_thread.finished.connect(self.candidate_thread.deleteLater)
        self.candidate_thread.finished.connect(self._candidate_thread_finished)
        self.candidate_thread.start()

    def prepare_automatic_recheck_all(self) -> None:
        if self.candidate_thread and self.candidate_thread.isRunning():
            return
        self._candidate_mode = "recheck_all"
        self.dashboard.automatic_recheck.setEnabled(False)
        self.set_status("Loading all orders", "#ef8b13")
        self.candidate_thread = QThread(self)
        self.candidate_worker = AutomaticCandidateWorker(self.controller, include_all=True)
        self.candidate_worker.moveToThread(self.candidate_thread)
        self.candidate_thread.started.connect(self.candidate_worker.run)
        self.candidate_worker.loaded.connect(self._automatic_candidates_loaded)
        self.candidate_worker.failed.connect(self._automatic_candidates_failed)
        self.candidate_worker.loaded.connect(self.candidate_thread.quit)
        self.candidate_worker.failed.connect(self.candidate_thread.quit)
        self.candidate_thread.finished.connect(self.candidate_worker.deleteLater)
        self.candidate_thread.finished.connect(self.candidate_thread.deleteLater)
        self.candidate_thread.finished.connect(self._candidate_thread_finished)
        self.candidate_thread.start()

    def _automatic_candidates_loaded(self, orders) -> None:
        self.set_status("Ready", "#2eaa50")
        if self._candidate_mode == "recheck_all":
            self.dashboard.automatic_recheck.setEnabled(True)
            if not orders:
                QMessageBox.information(self, "Automatic Recheck All", "No DMS orders were found in the workbook.")
                return
            answer = QMessageBox.question(
                self, "Confirm Automatic Recheck All",
                f"Recheck all {len(orders)} DMS order{'s' if len(orders) != 1 else ''} now?\n\n"
                "Every order will be checked again and the latest Excel verification results will be updated.",
            )
            if answer == QMessageBox.Yes:
                self.start_verification(orders, "Automatic Recheck")
            return
        if not orders:
            self.automatic.set_result("No new DMS orders found.")
            return
        self.automatic.set_result(f"{len(orders)} new DMS order{'s' if len(orders) != 1 else ''} found.")
        answer = QMessageBox.question(
            self, "Confirm Automatic Search",
            f"Verify {len(orders)} new DMS order{'s' if len(orders) != 1 else ''} now?",
        )
        if answer == QMessageBox.Yes:
            self.start_verification(orders, "Automatic")

    def _automatic_candidates_failed(self, message: str) -> None:
        self.set_status("Error", "#d90416")
        if self._candidate_mode == "recheck_all":
            self.dashboard.automatic_recheck.setEnabled(True)
            QMessageBox.critical(self, "Automatic Recheck All", message)
            return
        self.automatic.set_error("The search could not be completed. Check the workbook connection and try again.")
        QMessageBox.critical(self, "Automatic Search", message)

    def _candidate_thread_finished(self) -> None:
        self.candidate_thread = None
        self.candidate_worker = None

    def prepare_manual(self, numbers: list[str]) -> None:
        if self.manual_validation_thread and self.manual_validation_thread.isRunning():
            return
        self.set_status("Validating orders", "#ef8b13")
        self.stack.setCurrentWidget(self.progress)
        self.progress.start_indeterminate(
            "Validating order numbers",
            "Checking the selected order numbers against the workbook…",
        )
        self.manual_validation_thread = QThread(self)
        self.manual_validation_worker = ManualValidationWorker(self.controller, numbers)
        self.manual_validation_worker.moveToThread(self.manual_validation_thread)
        self.manual_validation_thread.started.connect(self.manual_validation_worker.run)
        self.manual_validation_worker.loaded.connect(self._manual_validation_loaded)
        self.manual_validation_worker.failed.connect(self._manual_validation_failed)
        self.manual_validation_worker.cancelled.connect(self._manual_validation_cancelled)
        for signal in (
            self.manual_validation_worker.loaded,
            self.manual_validation_worker.failed,
            self.manual_validation_worker.cancelled,
        ):
            signal.connect(self.manual_validation_thread.quit)
        self.manual_validation_thread.finished.connect(self.manual_validation_worker.deleteLater)
        self.manual_validation_thread.finished.connect(self.manual_validation_thread.deleteLater)
        self.manual_validation_thread.finished.connect(self._manual_validation_thread_finished)
        self.manual_validation_thread.start()

    def _manual_validation_loaded(self, orders, errors: dict[str, str]) -> None:
        self.set_status("Ready", "#2eaa50")
        if errors:
            if orders:
                details = "\n".join(f"• {message}" for message in errors.values())
                prompt = QMessageBox(self)
                prompt.setIcon(QMessageBox.Warning)
                prompt.setWindowTitle("Some orders were not available")
                prompt.setText(
                    f"{len(orders)} order{'s were' if len(orders) != 1 else ' was'} found and can be verified."
                )
                prompt.setInformativeText(
                    f"The following entries will be skipped:\n\n{details}\n\n"
                    "Would you like to continue with the orders that were found?"
                )
                continue_button = prompt.addButton("Verify Found Orders", QMessageBox.AcceptRole)
                prompt.addButton("Back to Manual Search", QMessageBox.RejectRole)
                prompt.exec()
                if prompt.clickedButton() is continue_button:
                    self.start_verification(orders, "Manual")
                    return
            self.manual.show_errors(errors)
            self.stack.setCurrentWidget(self.manual)
            return
        joined = "\n".join(order.number for order in orders)
        answer = QMessageBox.question(self, "Confirm Verification", f"Verify these DMS orders?\n\n{joined}")
        if answer == QMessageBox.Yes:
            self.start_verification(orders, "Manual")
        else:
            self.stack.setCurrentWidget(self.manual)

    def _manual_validation_failed(self, message: str) -> None:
        self.set_status("Error", "#d90416")
        self.stack.setCurrentWidget(self.manual)
        self.manual.show_errors({"workbook": message})

    def _manual_validation_cancelled(self) -> None:
        self.set_status("Ready", "#2eaa50")
        self.stack.setCurrentWidget(self.manual)

    def _manual_validation_thread_finished(self) -> None:
        self.manual_validation_thread = None
        self.manual_validation_worker = None

    def prepare_recheck(self, numbers: list[str]) -> None:
        orders, errors = self.controller.validate_manual_orders(numbers)
        if errors:
            QMessageBox.warning(self, "Recheck", "\n".join(errors.values()))
            return
        answer = QMessageBox.question(self, "Confirm Recheck", f"Recheck {len(orders)} selected order(s)?")
        if answer == QMessageBox.Yes:
            self.start_verification(orders, "Recheck")

    def start_verification(self, orders, mode: str) -> None:
        if not orders:
            return
        self.set_status("Searching", "#ef8b13")
        self.stack.setCurrentWidget(self.progress)
        self.thread = QThread(self)
        self.worker = VerificationWorker(self.controller, orders, mode)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress.update_progress)
        self.worker.finished.connect(self.verification_finished)
        self.worker.failed.connect(self.verification_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def cancel_current(self) -> None:
        if self.manual_validation_worker:
            self.manual_validation_worker.cancel()
            self.progress.operation_label.setText(
                "Cancellation requested — finishing the current workbook read…"
            )
        if self.worker:
            self.worker.cancel()
        if self.phase2_batch_worker:
            self.phase2_batch_worker.cancel()

    def verification_finished(self, results) -> None:
        self.set_status("Ready", "#2eaa50")
        self.results.set_results(results)
        self.stack.setCurrentWidget(self.results)
        self.refresh_dashboard()

    def verification_failed(self, message: str) -> None:
        self.set_status("Error", "#d90416")
        QMessageBox.critical(self, "Verification Error", message)
        self.navigate("dashboard")

    def show_history(self, status: str) -> None:
        self.history.set_filter(status if status != "all" else "")
        self.stack.setCurrentWidget(self.history)
        self.refresh_history({"query": "", "status": status if status != "all" else ""})

    def refresh_history(self, filters=None) -> None:
        filters = filters or {}
        query = filters.get("query", "")
        self.history.set_rows(self.controller.history.latest(
            status=filters.get("status") or None, query=query,
            order_category=filters.get("order_type") or None,
            period=filters.get("period", "all"), document=filters.get("document", ""),
            presence=filters.get("presence", ""),
        ))
        self.history.set_event_rows(self.controller.phase2_events(query))

    def show_phase2(self, so: str = "") -> None:
        try:
            self.phase2_page.set_orders(self.controller.phase2_orders())
        except Exception as exc:
            logging.getLogger(__name__).exception("Could not load Phase 2 orders")
            self.phase2_page.show_error(str(exc))
        try:
            self.stack.setCurrentWidget(self.phase2_page)
            if so:
                self.phase2_page.order.setCurrentText(so)
                self.prepare_phase2_preview(so)
        except Exception as exc:
            logging.getLogger(__name__).exception("Phase 2 navigation failed")
            self.phase2_page.show_error(f"Phase 2 could not open: {exc}")

    def prepare_phase2_batch(self, order_numbers) -> None:
        numbers = [str(value).strip() for value in order_numbers if str(value).strip()]
        if not numbers or (self.phase2_batch_thread and self.phase2_batch_thread.isRunning()):
            return
        self.set_status("Phase 2 batch", "#ef8b13")
        self.stack.setCurrentWidget(self.progress)
        self.phase2_batch_thread = QThread(self)
        self.phase2_batch_worker = Phase2BatchWorker(self.controller, numbers)
        self.phase2_batch_worker.moveToThread(self.phase2_batch_thread)
        self.phase2_batch_thread.started.connect(self.phase2_batch_worker.run)
        self.phase2_batch_worker.progress.connect(self.progress.update_progress)
        self.phase2_batch_worker.finished.connect(self._phase2_batch_finished)
        self.phase2_batch_worker.failed.connect(self._phase2_batch_failed)
        self.phase2_batch_worker.cancelled.connect(self._phase2_batch_cancelled)
        for signal in (
            self.phase2_batch_worker.finished,
            self.phase2_batch_worker.failed,
            self.phase2_batch_worker.cancelled,
        ):
            signal.connect(self.phase2_batch_thread.quit)
        self.phase2_batch_thread.finished.connect(self.phase2_batch_worker.deleteLater)
        self.phase2_batch_thread.finished.connect(self.phase2_batch_thread.deleteLater)
        self.phase2_batch_thread.finished.connect(self._phase2_batch_thread_finished)
        self.phase2_batch_thread.start()

    def _phase2_batch_finished(self, prepared) -> None:
        self.phase2_page.set_batch_runs(prepared)
        self.stack.setCurrentWidget(self.phase2_page)
        self.set_status("Ready", "#2eaa50")

    def _phase2_batch_failed(self, message: str) -> None:
        self.set_status("Error", "#d90416")
        QMessageBox.critical(self, "Phase 2 batch", message)
        self.stack.setCurrentWidget(self.results)

    def _phase2_batch_cancelled(self) -> None:
        self.set_status("Ready", "#2eaa50")
        self.stack.setCurrentWidget(self.results)

    def _phase2_batch_thread_finished(self) -> None:
        self.phase2_batch_thread = None
        self.phase2_batch_worker = None

    def prepare_phase2_preview(self, so: str) -> None:
        if not so or (self.phase2_thread and self.phase2_thread.isRunning()):
            return
        self.phase2_page.set_busy(True, f"Scanning Project SO {so}…")
        self.set_status("Phase 2 scanning", "#ef8b13")
        self.phase2_thread = QThread(self)
        self.phase2_worker = Phase2PreviewWorker(self.controller, so)
        self.phase2_worker.moveToThread(self.phase2_thread)
        self.phase2_thread.started.connect(self.phase2_worker.run)
        self.phase2_worker.progress.connect(self.phase2_page.set_progress)
        self.phase2_worker.finished.connect(self._phase2_preview_finished)
        self.phase2_worker.failed.connect(self._phase2_preview_failed)
        self.phase2_worker.cancelled.connect(self._phase2_preview_cancelled)
        for signal in (self.phase2_worker.finished, self.phase2_worker.failed, self.phase2_worker.cancelled):
            signal.connect(self.phase2_thread.quit)
        self.phase2_thread.finished.connect(self.phase2_worker.deleteLater)
        self.phase2_thread.finished.connect(self.phase2_thread.deleteLater)
        self.phase2_thread.finished.connect(self._phase2_thread_finished)
        self.phase2_thread.start()

    def cancel_phase2(self) -> None:
        if self.phase2_sync_worker:
            self.phase2_sync_worker.cancel()
            self.phase2_page.summary.setText("Sync cancellation requested; completing the current API operation safely…")
            return
        if self.phase2_worker:
            self.phase2_worker.cancel()
            self.phase2_page.summary.setText("Cancellation requested; waiting for the current file to finish safely…")

    def _phase2_preview_finished(self, run, online: bool) -> None:
        try:
            if run.__class__.__name__ == "FlowRun":
                self.phase2_page.set_flow_run(run)
            else:
                self.phase2_page.set_run(run, online)
            self.set_status("Ready", "#2eaa50")
        except Exception as exc:
            logging.getLogger(__name__).exception("Phase 2 result rendering failed")
            self._phase2_preview_failed(f"Phase 2 results could not be displayed: {exc}")

    def _phase2_preview_failed(self, message: str) -> None:
        self.phase2_page.show_error(message)
        self.set_status("Error", "#d90416")

    def _phase2_preview_cancelled(self) -> None:
        self.phase2_page.show_error("Phase 2 preview cancelled. No Smartsheet changes were made.")
        self.set_status("Ready", "#2eaa50")

    def edit_flow_review(self, review_id: int, field: str, value) -> None:
        try:
            self.controller.flow_review_edit(review_id, field, value)
        except Exception as exc:
            QMessageBox.critical(self, "Flow review edit", str(exc))

    def edit_flow_preview(self, review_id: int, serial_index: int, field: str, value: str) -> None:
        try:
            self.controller.flow_preview_edit(review_id, serial_index, field, value)
            run = self.phase2_page.current_flow_run
            if run is not None:
                run.review_records = self.controller.flow_reviews.load_items(run.run_id)
        except Exception as exc:
            QMessageBox.critical(self, "Flow Smartsheet preview edit", str(exc))

    def add_shared_flow_prefix(self) -> None:
        if self.phase2_thread and self.phase2_thread.isRunning():
            QMessageBox.information(self, "Add Prefix", "Wait for the current Phase 2 operation to finish.")
            return
        part, accepted = QInputDialog.getText(
            self, "Add shared Flow prefix", "Part number (for example CEH01026):"
        )
        if not accepted or not part.strip():
            return
        prefix, accepted = QInputDialog.getText(
            self, "Add shared Flow prefix", "Prefix to use in generated serials:"
        )
        if not accepted or not prefix.strip():
            return
        try:
            saved_part, saved_prefix = self.controller.add_flow_prefix(part, prefix)
        except Exception as exc:
            QMessageBox.critical(self, "Add Prefix", str(exc))
            return
        QMessageBox.information(
            self, "Prefix saved",
            f"{saved_part} → {saved_prefix} was saved in Sheet 2. "
            "All standalone apps will read it for future Flow previews.",
        )
        so = self.phase2_page.order.currentText().strip()
        if so:
            try:
                order = next((item for item in self.controller.phase2_orders() if item.so == so), None)
            except Exception as exc:
                QMessageBox.warning(self, "Refresh Flow preview", str(exc))
                return
            if order and order.category == "Flow":
                self.prepare_phase2_preview(so)

    def approve_flow_review(self, review_ids: set[int]) -> None:
        run = self.phase2_page.current_flow_run
        if not run or not review_ids:
            return
        self.phase2_page.set_busy(True, "0% — Reviewing selected Flow rows…")
        self.phase2_page.set_progress(25, "Validating selected Flow rows…")
        errors, records = self.controller.flow_review_approve(run.run_id, review_ids)
        self.phase2_page.set_progress(100, "Selected Flow rows reviewed")
        self.phase2_page.refresh_flow_records(run, records)
        if errors:
            message = "\n".join(f"Row {review_id}: {'; '.join(values)}" for review_id, values in errors.items())
            QMessageBox.warning(self, "Some rows were not approved", message)
        elif any(item.status.value == "approved" for _, item in records):
            # Approved Flow rows can be synchronized immediately. Rows still
            # needing review remain untouched and can be approved in a later
            # pass without holding back this prepared job.
            self.prepare_flow_final_preview(run)

    def exclude_flow_review(self, review_ids: set[int]) -> None:
        run = self.phase2_page.current_flow_run
        if not run or not review_ids:
            return
        reason, accepted = QInputDialog.getText(self, "Exclude Flow rows", "Reason for exclusion:")
        if not accepted or not reason.strip():
            return
        self.controller.flow_review_exclude(review_ids, reason.strip())
        self.phase2_page.refresh_flow_records(run, self.controller.flow_reviews.load_items(run.run_id))

    def prepare_flow_final_preview(self, run) -> None:
        if not run or (self.phase2_thread and self.phase2_thread.isRunning()):
            return
        self.phase2_page.set_busy(True, "Loading live Smartsheet and preparing the final Flow preview…")
        self.phase2_thread = QThread(self)
        self.phase2_worker = FlowFinalizeWorker(self.controller, run)
        self.phase2_worker.moveToThread(self.phase2_thread)
        self.phase2_thread.started.connect(self.phase2_worker.run)
        self.phase2_worker.progress.connect(self.phase2_page.set_progress)
        self.phase2_worker.finished.connect(self._flow_final_preview_finished)
        self.phase2_worker.failed.connect(self._phase2_preview_failed)
        self.phase2_worker.finished.connect(self.phase2_thread.quit)
        self.phase2_worker.failed.connect(self.phase2_thread.quit)
        self.phase2_thread.finished.connect(self.phase2_worker.deleteLater)
        self.phase2_thread.finished.connect(self.phase2_thread.deleteLater)
        self.phase2_thread.finished.connect(self._phase2_thread_finished)
        self.phase2_thread.start()

    def _flow_final_preview_finished(self, preview) -> None:
        self.phase2_page.replace_batch_run(preview, True)
        self.phase2_page.set_run(preview, True)
        self.set_status("Ready", "#2eaa50")

    def confirm_change_to_flow(self, run) -> None:
        if run is None or (self.phase2_thread and self.phase2_thread.isRunning()):
            return
        answer = QMessageBox.question(
            self, "Change workbook classification to Flow?",
            f"Order {run.order.so} is classified as Project, but only a recognised Flow-style DCL was found.\n\n"
            "Change Column A for this order to Flow in the production Excel workbook, then process it immediately "
            "with the Flow workflow?\n\nNo other workbook cells will be changed.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.phase2_page.set_busy(True, f"Changing order {run.order.so} to Flow and saving Excel safely…")
        self.phase2_thread = QThread(self)
        self.phase2_worker = ClassificationCorrectionWorker(self.controller, run.order.so)
        self.phase2_worker.moveToThread(self.phase2_thread)
        self.phase2_thread.started.connect(self.phase2_worker.run)
        self.phase2_worker.finished.connect(self._classification_changed)
        self.phase2_worker.failed.connect(self._phase2_preview_failed)
        self.phase2_worker.finished.connect(self.phase2_thread.quit)
        self.phase2_worker.failed.connect(self.phase2_thread.quit)
        self.phase2_thread.finished.connect(self.phase2_worker.deleteLater)
        self.phase2_thread.finished.connect(self.phase2_thread.deleteLater)
        self.phase2_thread.finished.connect(self._phase2_thread_finished)
        self.phase2_thread.start()

    def _classification_changed(self, so: str) -> None:
        self.phase2_page.set_orders(self.controller.phase2_orders())
        self.phase2_page.order.setCurrentText(so)
        self.set_status("Classification updated", "#2eaa50")
        self._pending_phase2_so = so

    def _phase2_thread_finished(self) -> None:
        self.phase2_thread = None
        self.phase2_worker = None
        if self._pending_phase2_so:
            so = self._pending_phase2_so
            self._pending_phase2_so = None
            self.prepare_phase2_preview(so)

    def confirm_phase2_sync(self, run) -> None:
        if run is None or (self.phase2_sync_thread and self.phase2_sync_thread.isRunning()):
            return
        writable = [row for row in run.plan_rows if row.writable]
        if not writable:
            QMessageBox.information(
                self, "Nothing new to synchronize",
                f"All prepared rows for {run.order.so} already exist, are excluded, or still need required data. "
                "No Smartsheet rows need to be created.",
            )
            self.phase2_page.summary.setText(f"No new approved rows to synchronize for {run.order.so}.")
            return
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("Confirm Production Smartsheet Sync")
        dialog.setText(
            f"This will apply {len(writable)} approved {run.order.category} row change(s) to the live Smartsheet."
        )
        dialog.setInformativeText(
            "Only new rows will be filled. Existing populated Smartsheet rows will never be changed or deleted. "
            "Continue?"
        )
        dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dialog.setDefaultButton(QMessageBox.No)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dialog.raise_()
        dialog.activateWindow()
        answer = dialog.exec()
        if answer != QMessageBox.Yes:
            self.phase2_page.summary.setText(f"Synchronization cancelled for {run.order.so}.")
            return
        self.phase2_page.set_busy(True, "Synchronizing approved rows and verifying readback…")
        self.set_status("Synchronizing", "#ef8b13")
        self.phase2_sync_thread = QThread(self)
        self.phase2_sync_worker = Phase2SyncWorker(self.controller, run)
        self.phase2_sync_worker.moveToThread(self.phase2_sync_thread)
        self.phase2_sync_thread.started.connect(self.phase2_sync_worker.run)
        self.phase2_sync_worker.progress.connect(self.phase2_page.set_progress)
        self.phase2_sync_worker.finished.connect(self._phase2_sync_finished)
        self.phase2_sync_worker.failed.connect(self._phase2_sync_failed)
        self.phase2_sync_worker.finished.connect(self.phase2_sync_thread.quit)
        self.phase2_sync_worker.failed.connect(self.phase2_sync_thread.quit)
        self.phase2_sync_thread.finished.connect(self.phase2_sync_worker.deleteLater)
        self.phase2_sync_thread.finished.connect(self.phase2_sync_thread.deleteLater)
        self.phase2_sync_thread.finished.connect(self._phase2_sync_thread_finished)
        self.phase2_sync_thread.start()

    def confirm_phase2_batch_sync(self, runs) -> None:
        if self.phase2_sync_thread and self.phase2_sync_thread.isRunning():
            return
        ready = [run for run in runs if hasattr(run, "plan_rows") and any(row.writable for row in run.plan_rows)]
        if not ready:
            QMessageBox.information(
                self, "Apply all prepared jobs",
                "None of the prepared jobs currently has approved writable rows.",
            )
            return
        total_rows = sum(sum(row.writable for row in run.plan_rows) for run in ready)
        job_list = "\n".join(f"• {run.order.so}" for run in ready)
        answer = QMessageBox.warning(
            self, "Confirm Batch Smartsheet Sync",
            f"Apply {total_rows} approved row change(s) for {len(ready)} prepared job(s)?\n\n{job_list}\n\n"
            "Each job will be written and verified before the next job begins. Existing populated rows will never "
            "be changed or deleted.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.phase2_page.set_busy(True, "Starting batch Smartsheet synchronization…")
        self.set_status("Batch synchronizing", "#ef8b13")
        self.phase2_sync_thread = QThread(self)
        self.phase2_sync_worker = Phase2BatchSyncWorker(self.controller, ready)
        self.phase2_sync_worker.moveToThread(self.phase2_sync_thread)
        self.phase2_sync_thread.started.connect(self.phase2_sync_worker.run)
        self.phase2_sync_worker.progress.connect(self.phase2_page.set_progress)
        self.phase2_sync_worker.finished.connect(self._phase2_sync_finished)
        self.phase2_sync_worker.failed.connect(self._phase2_sync_failed)
        self.phase2_sync_worker.finished.connect(self.phase2_sync_thread.quit)
        self.phase2_sync_worker.failed.connect(self.phase2_sync_thread.quit)
        self.phase2_sync_thread.finished.connect(self.phase2_sync_worker.deleteLater)
        self.phase2_sync_thread.finished.connect(self.phase2_sync_thread.deleteLater)
        self.phase2_sync_thread.finished.connect(self._phase2_sync_thread_finished)
        self.phase2_sync_thread.start()

    def _phase2_sync_finished(self, result) -> None:
        self.phase2_page.set_busy(False)
        self.phase2_page.sync_button.setEnabled(False)
        self.set_status("Ready", "#2eaa50")
        detail = "\n".join(result.errors)
        message = (
            f"Created: {result.created}\nUpdated: {result.updated}\nUnchanged: {result.unchanged}\n"
            f"Blocked: {result.blocked}\nReconciled after unknown response: {result.reconciled}\nFailed: {result.failed}"
        )
        if detail:
            message += f"\n\nError details:\n{detail}"
            logging.getLogger(__name__).error("Phase 2 synchronization errors: %s", detail)
        dialog = QMessageBox.warning if result.failed else QMessageBox.information
        dialog(self, "Phase 2 synchronization complete", message)

    def _phase2_sync_failed(self, message: str) -> None:
        self.phase2_page.show_error(f"Synchronization stopped: {message}")
        self.set_status("Error", "#d90416")

    def _phase2_sync_thread_finished(self) -> None:
        self.phase2_sync_thread = None
        self.phase2_sync_worker = None

    def closeEvent(self, event) -> None:
        if ((self.thread and self.thread.isRunning())
                or (self.dashboard_thread and self.dashboard_thread.isRunning())
                or (self.candidate_thread and self.candidate_thread.isRunning())
                or (self.phase2_batch_thread and self.phase2_batch_thread.isRunning())
                or (self.phase2_thread and self.phase2_thread.isRunning())
                or (self.phase2_sync_thread and self.phase2_sync_thread.isRunning())):
            QMessageBox.information(self, "Verification in progress", "Please cancel and wait for the current safe step to finish before closing.")
            event.ignore()
            return
        event.accept()

