from __future__ import annotations

from collections import Counter
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from app.phase2.models import IssueSeverity, Phase2DryRun, PlanAction, ValidationIssue
from app.phase2.sync_planner import APPROVED_FIELDS
from app.ui.widgets import Section


class Phase2ProjectPage(QWidget):
    preview_requested = Signal(str)
    cancel_requested = Signal()
    sync_requested = Signal(object)
    sync_all_requested = Signal(object)
    flow_edit_requested = Signal(int, str, object)
    flow_preview_edit_requested = Signal(int, int, str, str)
    flow_approve_requested = Signal(object)
    flow_exclude_requested = Signal(object)
    flow_finalize_requested = Signal(object)
    prefix_add_requested = Signal()
    classification_change_requested = Signal(object)
    back_requested = Signal()

    def __init__(self):
        super().__init__()
        self.current_run: Phase2DryRun | None = None
        self.current_flow_run = None
        self._batch_runs = {}
        self._project_online = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        section = Section("Phase 2 — Process Order", "The workbook classification automatically selects Project or Flow rules.")
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Order SO"))
        self.order = QComboBox()
        self.order.setEditable(True)
        self.order.setMinimumWidth(220)
        self.order.currentTextChanged.connect(self._show_cached_batch_run)
        controls.addWidget(self.order)
        self.preview_button = QPushButton("Prepare Preview")
        self.preview_button.setObjectName("primaryButton")
        self.preview_button.clicked.connect(lambda: self.preview_requested.emit(self.order.currentText().strip()))
        controls.addWidget(self.preview_button)
        self.add_prefix_button = QPushButton("Add Prefix")
        self.add_prefix_button.setObjectName("secondaryButton")
        self.add_prefix_button.setToolTip("Save a shared Flow part-number prefix in Sheet 2.")
        self.add_prefix_button.clicked.connect(self.prefix_add_requested)
        controls.addWidget(self.add_prefix_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_requested)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        back = QPushButton("Back")
        back.setObjectName("secondaryButton")
        back.clicked.connect(self.back_requested)
        controls.addWidget(back)
        section.layout.addLayout(controls)
        self.activity = QProgressBar()
        self.activity.setRange(0, 0)
        self.activity.setVisible(False)
        section.layout.addWidget(self.activity)
        self.summary = QLabel("Select an eligible DMS Project order to begin.")
        self.summary.setObjectName("completionSummary")
        section.layout.addWidget(self.summary)

        tabs = QTabWidget()
        self.documents = QTableWidget(0, 5)
        self.documents.setHorizontalHeaderLabels(["Category", "File", "Type", "Revision", "Parse status"])
        self.equipment = QTableWidget(0, 5)
        self.equipment.setHorizontalHeaderLabels(["Equipment", "Serial", "Colour", "Source", "State"])
        self.issues = QTableWidget(0, 3)
        self.issues.setHorizontalHeaderLabels(["Severity", "Code", "Reason"])
        self.issues.cellClicked.connect(self._show_issue_detail)
        self.preview_fields = [
            "Forecast Month", "Type", "SO No.", "Customer", "Incoterms", "Serial No", "Colour In/Ext",
        ]
        self.preview = QTableWidget(0, 3 + len(self.preview_fields))
        self.preview.setHorizontalHeaderLabels(["Action", "Equipment", *self.preview_fields, "Issues"])
        self.preview.itemChanged.connect(self._project_preview_changed)
        self.preview.cellClicked.connect(self._show_project_issue)
        self._loading_project_preview = False
        self.flow_review = QTableWidget(0, 10)
        self.flow_review.setHorizontalHeaderLabels([
            "ID", "Status", "Source", "Page", "Part", "Description", "Qty",
            "Classification", "Serial pattern", "Proposed serials",
        ])
        self.flow_review.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.flow_review.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.flow_review.setAlternatingRowColors(True)
        self.flow_review.horizontalHeader().setStretchLastSection(True)
        self.flow_review.itemChanged.connect(self._flow_item_changed)
        self._loading_flow = False
        for table in (self.documents, self.equipment, self.issues):
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setAlternatingRowColors(True)
            table.horizontalHeader().setStretchLastSection(True)
        self.preview.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.preview.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview.setAlternatingRowColors(True)
        self.preview.horizontalHeader().setStretchLastSection(True)
        tabs.addTab(self.documents, "Documents")
        tabs.addTab(self.equipment, "Equipment")
        tabs.addTab(self.issues, "Issues")
        tabs.addTab(self.preview, "Smartsheet Preview")
        self.flow_tab_index = tabs.addTab(self.flow_review, "Flow Review")
        self.tabs = tabs
        section.layout.addWidget(tabs, 1)
        self.project_help = QLabel(
            "Blocked row options: edit the missing value, exclude the row from this sync, "
            "or leave it blocked while syncing the other approved rows."
        )
        self.project_help.setWordWrap(True)
        self.project_help.setVisible(False)
        section.layout.addWidget(self.project_help)
        project_actions = QHBoxLayout()
        project_actions.addStretch()
        self.resolve_project_button = QPushButton("Edit Selected Blocked Row")
        self.resolve_project_button.setObjectName("primaryButton")
        self.resolve_project_button.clicked.connect(self._resolve_selected_project_row)
        self.override_project_button = QPushButton("Override Selected Block")
        self.override_project_button.setObjectName("primaryButton")
        self.override_project_button.clicked.connect(self._override_selected_project_row)
        self.exclude_project_button = QPushButton("Exclude Selected Row…")
        self.exclude_project_button.setObjectName("secondaryButton")
        self.exclude_project_button.clicked.connect(self._exclude_selected_project_row)
        self.restore_project_button = QPushButton("Restore Selected Row")
        self.restore_project_button.setObjectName("secondaryButton")
        self.restore_project_button.clicked.connect(self._restore_selected_project_row)
        project_actions.addWidget(self.resolve_project_button)
        project_actions.addWidget(self.override_project_button)
        project_actions.addWidget(self.exclude_project_button)
        project_actions.addWidget(self.restore_project_button)
        section.layout.addLayout(project_actions)
        review_actions = QHBoxLayout()
        self.approve_button = QPushButton("Approve Selected")
        self.approve_button.setObjectName("primaryButton")
        self.approve_button.clicked.connect(lambda: self.flow_approve_requested.emit(self.selected_flow_ids()))
        self.exclude_button = QPushButton("Exclude Selected…")
        self.exclude_button.setObjectName("secondaryButton")
        self.exclude_button.clicked.connect(lambda: self.flow_exclude_requested.emit(self.selected_flow_ids()))
        self.finalize_button = QPushButton("Prepare Approved Rows Preview")
        self.finalize_button.setObjectName("primaryButton")
        self.finalize_button.clicked.connect(lambda: self.flow_finalize_requested.emit(self.current_flow_run))
        self.approve_button.setVisible(False)
        self.exclude_button.setVisible(False)
        self.finalize_button.setVisible(False)
        for widget in (
            self.resolve_project_button, self.override_project_button,
            self.exclude_project_button, self.restore_project_button,
        ):
            widget.setVisible(True)
        review_actions.addStretch()
        review_actions.addWidget(self.approve_button)
        review_actions.addWidget(self.exclude_button)
        review_actions.addWidget(self.finalize_button)
        section.layout.addLayout(review_actions)
        self.change_to_flow_button = QPushButton("Change Excel to Flow and Retry")
        self.change_to_flow_button.setObjectName("primaryButton")
        self.change_to_flow_button.setVisible(False)
        self.change_to_flow_button.clicked.connect(
            lambda: self.classification_change_requested.emit(self.current_run)
        )
        section.layout.addWidget(self.change_to_flow_button)
        self.sync_button = QPushButton("Confirm and Sync Approved Rows")
        self.sync_button.setObjectName("primaryButton")
        self.sync_button.setEnabled(False)
        self.sync_button.clicked.connect(self._request_current_sync)
        self.sync_all_button = QPushButton("Apply All Prepared Jobs")
        self.sync_all_button.setObjectName("primaryButton")
        self.sync_all_button.setVisible(False)
        self.sync_all_button.clicked.connect(
            lambda: self.sync_all_requested.emit([item[0] for item in self._batch_runs.values()])
        )
        sync_actions = QHBoxLayout()
        sync_actions.addWidget(self.sync_button)
        sync_actions.addWidget(self.sync_all_button)
        section.layout.addLayout(sync_actions)
        layout.addWidget(section, 1)

    def set_orders(self, orders) -> None:
        self._batch_runs = {}
        self.sync_all_button.setVisible(False)
        self.sync_button.setText("Confirm and Sync Approved Rows")
        current = self.order.currentText()
        self.order.clear()
        self.order.addItems([item.so for item in orders])
        for index, item in enumerate(orders):
            self.order.setItemData(index, item.category)
        if current:
            self.order.setCurrentText(current)

    def set_batch_runs(self, prepared) -> None:
        self._batch_runs = {run.order.so: (run, online) for run, online in prepared}
        self.order.blockSignals(True)
        self.order.clear()
        self.order.addItems(list(self._batch_runs))
        self.order.blockSignals(False)
        if self.order.count():
            self.order.setCurrentIndex(0)
            self._show_cached_batch_run(self.order.currentText())
        self.sync_all_button.setVisible(len(self._batch_runs) > 1)
        self.sync_button.setText("Apply Current Job" if len(self._batch_runs) > 1 else "Confirm and Sync Approved Rows")
        self._update_batch_sync_button()

    def _show_cached_batch_run(self, so: str) -> None:
        prepared = self._batch_runs.get(so)
        if not prepared:
            return
        run, online = prepared
        if run.__class__.__name__ == "FlowRun":
            self.set_flow_run(run)
        else:
            self.set_run(run, online)
        self._update_batch_sync_button()

    def _update_batch_sync_button(self) -> None:
        self.sync_all_button.setEnabled(any(
            online and any(row.writable for row in run.plan_rows)
            for run, online in self._batch_runs.values()
            if hasattr(run, "plan_rows")
        ))

    def _request_current_sync(self) -> None:
        if self.current_run is None:
            self.summary.setText("Prepare a final Smartsheet preview before synchronizing.")
            return
        self.summary.setText(f"Opening synchronization confirmation for {self.current_run.order.so}…")
        self.sync_requested.emit(self.current_run)

    def replace_batch_run(self, run, online: bool) -> None:
        """Keep a finalized Flow preview in the batch selected by the user."""
        so = run.order.so
        if so in self._batch_runs:
            self._batch_runs[so] = (run, online)
            self._update_batch_sync_button()

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.activity.setVisible(busy)
        self.activity.setRange(0, 100)
        if busy:
            self.activity.setValue(0)
            self.activity.setFormat("0%")
        self.preview_button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.sync_button.setEnabled(False if busy else self.sync_button.isEnabled())
        if message:
            self.summary.setText(message)

    def set_progress(self, percent: int, message: str = "") -> None:
        value = max(0, min(100, int(percent)))
        self.activity.setRange(0, 100)
        self.activity.setVisible(True)
        self.activity.setValue(value)
        self.activity.setFormat(f"{value}%")
        if message:
            self.summary.setText(message)

    @staticmethod
    def _fill(table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row_index, column, QTableWidgetItem(value))
        table.resizeColumnsToContents()

    def set_run(self, run: Phase2DryRun, online: bool) -> None:
        self.current_run = run
        self.current_flow_run = None
        self._project_online = online
        self.approve_button.setVisible(False)
        self.exclude_button.setVisible(False)
        self.finalize_button.setVisible(False)
        for widget in (
            self.resolve_project_button, self.override_project_button,
            self.exclude_project_button, self.restore_project_button,
        ):
            widget.setVisible(True)
        mismatch = any(issue.code == "WORKBOOK_CLASSIFICATION_MISMATCH" for issue in run.issues)
        self.change_to_flow_button.setVisible(mismatch)
        actions = Counter(row.action.value for row in run.plan_rows)
        blocking_issues = sum(issue.severity.value == "blocking" for issue in run.issues)
        self.summary.setText(
            f"SO {run.order.so}  •  {len(run.documents)} documents  •  {len(run.equipment)} equipment rows  •  "
            f"{actions.get('create', 0)} create  •  {actions.get('update', 0)} update  •  "
            f"{actions.get('blocked', 0)} blocked rows  •  {blocking_issues} blocking issues  •  "
            f"{'Live schema checked' if online else 'Offline preview'}"
        )
        self._fill(self.documents, [[
            item.category, item.filename, item.detected_type, item.revision or "—", item.parse_status
        ] for item in run.documents])
        self._fill(self.equipment, [[
            item.equipment_type, item.serial or "Pending", item.colour or "—",
            item.serial_source or "Derived rule", "Blocked" if item.blocked else "Ready",
        ] for item in run.equipment])
        all_issues = list(run.issues) + [issue for item in run.equipment for issue in item.issues]
        self._fill(self.issues, [[issue.severity.value, issue.code, issue.message] for issue in all_issues])
        self._load_project_preview(run)
        self.activity.setVisible(False)
        self.preview_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        # Keep the control actionable for every online final preview. The main
        # window explains when all rows already exist instead of presenting a
        # disabled button that appears not to work.
        self.sync_button.setEnabled(online and bool(run.plan_rows))
        if not run.equipment and all_issues:
            self.summary.setText(
                self.summary.text() + f"  •  {len(all_issues)} review issue{'s' if len(all_issues) != 1 else ''}"
            )
            self.tabs.setCurrentWidget(self.issues)
        else:
            self.tabs.setCurrentWidget(self.preview)

    def _load_project_preview(self, run: Phase2DryRun) -> None:
        self._loading_project_preview = True
        self.preview.setRowCount(len(run.plan_rows))
        for row_index, plan in enumerate(run.plan_rows):
            values = [plan.action.value, plan.equipment_type]
            values.extend(plan.proposed_values.get(field, "") for field in self.preview_fields)
            values.append("; ".join(issue.message for issue in plan.issues))
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column < 2 or column == len(values) - 1:
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                    if column == len(values) - 1 and value:
                        cell.setToolTip(f"Click to view full discrepancy:\n{value}")
                else:
                    cell.setData(Qt.UserRole, self.preview_fields[column - 2])
                    cell.setToolTip("Double-click to edit the value that will be sent to Smartsheet.")
                self.preview.setItem(row_index, column, cell)
        self.preview.resizeColumnsToContents()
        self._loading_project_preview = False

    def _project_preview_changed(self, cell: QTableWidgetItem) -> None:
        if self._loading_project_preview:
            return
        if self.current_run is None and self.current_flow_run is not None:
            review_id = cell.data(Qt.UserRole + 1)
            serial_index = cell.data(Qt.UserRole + 2)
            field = cell.data(Qt.UserRole)
            if review_id is not None and serial_index is not None and field in APPROVED_FIELDS:
                self.flow_preview_edit_requested.emit(
                    int(review_id), int(serial_index), str(field), cell.text().strip(),
                )
            return
        if self.current_run is None:
            return
        field = cell.data(Qt.UserRole)
        if not field or field not in APPROVED_FIELDS or cell.row() >= len(self.current_run.plan_rows):
            return
        plan = self.current_run.plan_rows[cell.row()]
        value = cell.text().strip()
        if value:
            plan.proposed_values[field] = value
        else:
            plan.proposed_values.pop(field, None)
        if field == "Serial No":
            plan.serial = value or None

        resolved_codes = {
            "Serial No": "MISSING_SERIAL",
            "Customer": "MISSING_COMMERCIAL_CUSTOMER",
            "Incoterms": "MISSING_INCOTERMS",
        }
        resolved = resolved_codes.get(field)
        if value and resolved:
            plan.issues = [issue for issue in plan.issues if issue.code != resolved]
        if plan.action == PlanAction.EXCLUDED:
            return
        blocking = any(issue.severity == IssueSeverity.BLOCKING for issue in plan.issues)
        if blocking:
            plan.action = PlanAction.BLOCKED
        elif plan.existing_row_id is None:
            plan.action = PlanAction.CREATE
        else:
            plan.action = PlanAction.UNCHANGED
        self.preview.item(cell.row(), 0).setText(plan.action.value)
        self.preview.item(cell.row(), self.preview.columnCount() - 1).setText(
            "; ".join(issue.message for issue in plan.issues)
        )
        self.sync_button.setEnabled(self._project_online and bool(self.current_run.plan_rows))

    def _selected_project_row(self) -> int | None:
        selected = self.preview.selectionModel().selectedRows()
        if selected:
            return selected[0].row()
        if self.current_run:
            for index, plan in enumerate(self.current_run.plan_rows):
                if plan.action == PlanAction.BLOCKED:
                    self.preview.selectRow(index)
                    return index
        return None

    def _show_project_issue(self, row: int, column: int) -> None:
        if column != self.preview.columnCount() - 1:
            return
        cell = self.preview.item(row, column)
        if cell and cell.text().strip():
            QMessageBox.information(self, "Discrepancy details", cell.text())

    def _show_issue_detail(self, row: int, column: int) -> None:
        if column != 2:
            return
        cell = self.issues.item(row, column)
        if cell and cell.text().strip():
            QMessageBox.information(self, "Issue details", cell.text())

    def _override_selected_project_row(self) -> None:
        row = self._selected_project_row()
        if row is None or self.current_run is None:
            self.summary.setText("Select a blocked Smartsheet preview row first.")
            return
        plan = self.current_run.plan_rows[row]
        blocking = [issue for issue in plan.issues if issue.severity == IssueSeverity.BLOCKING]
        if not blocking:
            self.summary.setText("The selected row has no blocking discrepancy to override.")
            return
        answer = QMessageBox.question(
            self,
            "Override blocked row",
            "Use the edited preview values and allow this row to synchronize despite the listed discrepancy?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        plan.issues = [
            ValidationIssue(
                issue.code,
                IssueSeverity.WARNING if issue.severity == IssueSeverity.BLOCKING else issue.severity,
                issue.message,
                issue.equipment_type,
                issue.serial,
            )
            for issue in plan.issues
        ]
        plan.issues.append(ValidationIssue(
            code="USER_OVERRIDE_APPROVED",
            severity=IssueSeverity.INFO,
            message="Blocking discrepancy was explicitly overridden for this synchronization.",
            equipment_type=plan.equipment_type,
            serial=plan.serial,
        ))
        plan.action = PlanAction.CREATE if plan.existing_row_id is None else PlanAction.UNCHANGED
        self._load_project_preview(self.current_run)
        self.preview.selectRow(row)
        self.sync_button.setEnabled(self._project_online and bool(self.current_run.plan_rows))
        self._update_batch_sync_button()
        self.summary.setText(
            f"Override approved for {plan.serial or plan.equipment_type}. The row can now synchronize."
        )

    def _resolve_selected_project_row(self) -> None:
        row = self._selected_project_row()
        if row is None or self.current_run is None:
            self.summary.setText("Select a blocked Smartsheet preview row first.")
            return
        plan = self.current_run.plan_rows[row]
        codes = {issue.code for issue in plan.issues}
        field = "Serial No"
        if "MISSING_COMMERCIAL_CUSTOMER" in codes:
            field = "Customer"
        elif "MISSING_INCOTERMS" in codes:
            field = "Incoterms"
        column = 2 + self.preview_fields.index(field)
        self.tabs.setCurrentWidget(self.preview)
        self.preview.setCurrentCell(row, column)
        self.preview.editItem(self.preview.item(row, column))
        self.summary.setText(
            f"Editing {field} for {plan.equipment_type}. Enter the approved value; the row revalidates automatically."
        )

    def _exclude_selected_project_row(self) -> None:
        row = self._selected_project_row()
        if row is None or self.current_run is None:
            self.summary.setText("Select a Smartsheet preview row to exclude.")
            return
        reason, accepted = QInputDialog.getText(self, "Exclude Project row", "Reason for exclusion:")
        if not accepted or not reason.strip():
            return
        plan = self.current_run.plan_rows[row]
        plan.action = PlanAction.EXCLUDED
        plan.issues.append(ValidationIssue(
            code="USER_EXCLUDED", severity=IssueSeverity.INFO,
            message=f"Excluded from this sync: {reason.strip()}",
            equipment_type=plan.equipment_type, serial=plan.serial,
        ))
        self._load_project_preview(self.current_run)
        self.sync_button.setEnabled(self._project_online and bool(self.current_run.plan_rows))

    def _restore_selected_project_row(self) -> None:
        row = self._selected_project_row()
        if row is None or self.current_run is None:
            return
        plan = self.current_run.plan_rows[row]
        plan.issues = [issue for issue in plan.issues if issue.code != "USER_EXCLUDED"]
        if any(issue.severity == IssueSeverity.BLOCKING for issue in plan.issues):
            plan.action = PlanAction.BLOCKED
        else:
            plan.action = PlanAction.CREATE if plan.existing_row_id is None else PlanAction.UNCHANGED
        self._load_project_preview(self.current_run)
        self.sync_button.setEnabled(self._project_online and bool(self.current_run.plan_rows))

    def set_flow_run(self, run) -> None:
        self.current_run = None
        self.current_flow_run = run
        for widget in (
            self.resolve_project_button, self.override_project_button,
            self.exclude_project_button, self.restore_project_button,
        ):
            widget.setVisible(False)
        self.change_to_flow_button.setVisible(False)
        records = run.review_records or list(enumerate(run.review_items, 1))
        counts = Counter(item.status.value for _, item in records)
        self.summary.setText(
            f"SO {run.order.so}  •  Flow  •  DCL-only  •  {len(run.dcl_files)} DCL files  •  "
            f"{len(records)} material lines  •  {counts.get('needs_review', 0)} need review  •  "
            f"{counts.get('blocked', 0)} blocked"
        )
        self._fill(self.documents, [["DCL", path.name, "despatch_checklist", "—", "parsed"] for path in run.dcl_files])
        self._fill(self.equipment, [[
            item.category or "Unknown", ", ".join(item.proposed_serials) or "Pending",
            "—", f"{item.line.source_file} page {item.line.page}", item.status.value,
        ] for _, item in records])
        self._fill(self.issues, [["review", "FLOW_REVIEW", issue] for issue in run.issues] + [
            ["review", "MATERIAL", issue] for _, item in records for issue in item.issues
        ])
        self._load_flow_serial_preview(run, records)
        self._load_flow_review(records)
        # The job selector is a Smartsheet-review workflow. Always reopen a
        # Flow job on its generated Smartsheet preview; the Flow Review tab
        # remains available for approving or editing individual source rows.
        self.tabs.setCurrentWidget(self.preview)
        self.approve_button.setVisible(True)
        self.exclude_button.setVisible(True)
        self.finalize_button.setVisible(True)
        self.activity.setVisible(False)
        self.preview_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        self.sync_button.setEnabled(False)

    def _load_flow_serial_preview(self, run, records) -> None:
        """Show one Project-style preview row for every proposed Flow serial."""
        rows = []
        forecast = date.today().strftime("%d-%b")
        row_sources = []
        for review_id, item in records:
            serials = item.proposed_serials or [""]
            for serial_index, serial in enumerate(serials):
                overrides = item.proposed_value_overrides.get(str(serial_index), {})
                rows.append([
                    "review" if item.status.value != "approved" else "approved",
                    item.category or item.line.part_code or "Unknown",
                    overrides.get("Forecast Month", forecast),
                    overrides.get("Type", "Flow"),
                    overrides.get("SO No.", run.order.so),
                    overrides.get("Customer", item.line.customer or ""),
                    overrides.get("Incoterms", run.order.term or ""),
                    overrides.get("Serial No", serial or "Pending"),
                    overrides.get("Colour In/Ext", ""),
                    "; ".join(item.issues),
                ])
                row_sources.append((review_id, serial_index))
        self._loading_project_preview = True
        self._fill(self.preview, rows)
        for row in range(self.preview.rowCount()):
            for column in range(self.preview.columnCount()):
                cell = self.preview.item(row, column)
                if cell:
                    if 2 <= column < 2 + len(self.preview_fields):
                        cell.setData(Qt.UserRole, self.preview_fields[column - 2])
                        cell.setData(Qt.UserRole + 1, row_sources[row][0])
                        cell.setData(Qt.UserRole + 2, row_sources[row][1])
                        cell.setToolTip("Double-click to edit the value that will be sent to Smartsheet.")
                    else:
                        cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
        self.preview.resizeColumnsToContents()
        self._loading_project_preview = False

    def _load_flow_review(self, records) -> None:
        self._loading_flow = True
        self.flow_review.setRowCount(len(records))
        editable = {7: "category", 8: "serial_pattern", 9: "proposed_serials_json"}
        for row, (review_id, item) in enumerate(records):
            values = [
                str(review_id), item.status.value, item.line.source_file, str(item.line.page),
                item.line.part_code or "—", item.line.description,
                f"{item.line.quantity or ''} {item.line.unit or ''}".strip(), item.category or "",
                item.serial_pattern or "", "; ".join(item.proposed_serials),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(256, editable.get(column))
                if column not in editable:
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                else:
                    cell.setToolTip("Double-click to edit this Flow value before approval and synchronization.")
                self.flow_review.setItem(row, column, cell)
        self.flow_review.setColumnHidden(0, True)
        self.flow_review.resizeColumnsToContents()
        if records:
            self.flow_review.selectRow(0)
        self._loading_flow = False

    def _flow_item_changed(self, cell: QTableWidgetItem) -> None:
        if self._loading_flow:
            return
        field = cell.data(256)
        if not field:
            return
        review_id = int(self.flow_review.item(cell.row(), 0).text())
        value = cell.text().strip()
        if field == "proposed_serials_json":
            value = [part.strip() for part in value.replace("\n", ";").split(";") if part.strip()]
        self.flow_edit_requested.emit(review_id, field, value)

    def selected_flow_ids(self) -> set[int]:
        selected = {
            int(self.flow_review.item(index.row(), 0).text())
            for index in self.flow_review.selectionModel().selectedRows()
        }
        if selected:
            return selected
        # A single click on Approve must never silently do nothing. With no
        # explicit selection, treat every visible review row as selected.
        return {
            int(self.flow_review.item(row, 0).text())
            for row in range(self.flow_review.rowCount())
        }

    def refresh_flow_records(self, run, records) -> None:
        run.review_records = records
        self.set_flow_run(run)

    def show_error(self, message: str) -> None:
        self.set_busy(False)
        self.summary.setText(message)
