from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from app.ui.widgets import Section, SummaryCard


class ResultsPage(QWidget):
    finish_requested = Signal()
    history_requested = Signal()
    phase2_requested = Signal(object)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        section = Section("Phase 1 verification completed", "Review document results and Excel persistence status separately.")
        self.summary = QLabel()
        self.summary.setObjectName("completionSummary")
        section.layout.addWidget(self.summary)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Order", "Type", "Specs", "QA", "DCL", "Drawings", "FAT", "Status", "Excel", "Details",
        ])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        section.layout.addWidget(self.table, 1)
        question = QLabel("Phase 1 verification completed. Proceed to Phase 2?")
        question.setObjectName("sectionTitle")
        section.layout.addWidget(question)
        controls = QHBoxLayout()
        history = QPushButton("View Full History")
        history.setObjectName("secondaryButton")
        history.clicked.connect(self.history_requested)
        finish = QPushButton("Finish Here")
        finish.setObjectName("secondaryButton")
        finish.clicked.connect(self.finish_requested)
        phase2 = QPushButton("Proceed to Phase 2")
        phase2.setObjectName("primaryButton")
        phase2.clicked.connect(self._proceed_to_phase2)
        controls.addWidget(history)
        controls.addStretch()
        controls.addWidget(finish)
        controls.addWidget(phase2)
        section.layout.addLayout(controls)
        layout.addWidget(section, 1)

    def set_results(self, results) -> None:
        self._results = list(results)
        verified = sum(result.overall_status == "Completed Pass" for result in results)
        missing = sum(result.overall_status == "Missing Documents" for result in results)
        errors = sum(result.overall_status == "Error" for result in results)
        saved = sum(result.excel_update_status == "Saved" for result in results)
        self.summary.setText(
            f"{len(results)} checked   •   {verified} fully verified   •   {missing} missing documents   •   "
            f"{errors} errors   •   {saved}/{len(results)} saved to Excel"
        )
        self.table.setRowCount(len(results))
        for row_index, result in enumerate(results):
            values = [
                result.order.number, result.order.category, result.specs, result.qa, result.dcl,
                result.drawings, result.fat, result.overall_status, result.excel_update_status,
                "\n".join(result.errors),
            ]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()

    def _proceed_to_phase2(self) -> None:
        selected = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if not selected and getattr(self, "_results", None):
            selected = list(range(self.table.rowCount()))
        if not selected:
            return
        self.phase2_requested.emit([self.table.item(row, 0).text() for row in selected])


