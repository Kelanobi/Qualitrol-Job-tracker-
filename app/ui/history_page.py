from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from app.ui.widgets import Section


class HistoryPage(QWidget):
    refresh_requested = Signal(object)
    recheck_requested = Signal(list)
    back_requested = Signal()
    detail_requested = Signal(str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        top = QHBoxLayout()
        top.addStretch()
        back = QPushButton("Back")
        back.setObjectName("secondaryButton")
        back.clicked.connect(self.back_requested)
        top.addWidget(back)
        layout.addLayout(top)
        section = Section("History", "Review the latest status or select orders to recheck.")
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search order number")
        self.search.setClearButtonEnabled(True)
        self.status = QComboBox()
        self.status.addItems(["All statuses", "Completed Pass", "Missing Documents"])
        self.order_type = QComboBox()
        self.order_type.addItems(["All types", "Project", "Flow"])
        self.period = QComboBox()
        self.period.addItem("All dates", "all")
        self.period.addItem("Current week", "current_week")
        self.period.addItem("Previous week", "previous_week")
        self.period.addItem("Current month", "current_month")
        self.period.addItem("Previous month", "previous_month")
        refresh = QPushButton("Apply Filters")
        refresh.setObjectName("smallButton")
        refresh.clicked.connect(self._refresh)
        self.search.returnPressed.connect(self._refresh)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.status)
        filters.addWidget(self.order_type)
        filters.addWidget(self.period)
        filters.addWidget(refresh)
        section.layout.addLayout(filters)
        self.filter_message = QLabel()
        self.filter_message.setObjectName("muted")
        section.layout.addWidget(self.filter_message)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "Order", "Type", "Specs", "QA", "DCL", "Drawings", "FAT", "Status", "Checked",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(
            lambda row, _column: self.detail_requested.emit(self.table.item(row, 0).text())
        )
        self.events = QTableWidget(0, 6)
        self.events.setHorizontalHeaderLabels(["Order", "Type", "Event", "Outcome", "Actor", "Date"])
        self.events.setEditTriggers(QTableWidget.NoEditTriggers)
        self.events.setAlternatingRowColors(True)
        self.events.horizontalHeader().setStretchLastSection(True)
        tabs = QTabWidget()
        tabs.addTab(self.table, "Latest verification")
        tabs.addTab(self.events, "Phase 2 activity")
        section.layout.addWidget(tabs, 1)
        controls = QHBoxLayout()
        self.count_label = QLabel("0 orders")
        controls.addWidget(self.count_label)
        controls.addStretch()
        recheck = QPushButton("Recheck Selected")
        recheck.setObjectName("primaryButton")
        recheck.clicked.connect(self._recheck)
        controls.addWidget(recheck)
        section.layout.addLayout(controls)
        layout.addWidget(section, 1)

    def _refresh(self) -> None:
        status = "" if self.status.currentIndex() == 0 else self.status.currentText()
        order_type = "" if self.order_type.currentIndex() == 0 else self.order_type.currentText()
        self.refresh_requested.emit({
            "query": self.search.text(), "status": status, "order_type": order_type,
            "period": self.period.currentData(), "document": "", "presence": "",
        })

    def set_filter(self, status: str) -> None:
        index = self.status.findText(status)
        self.status.setCurrentIndex(index if index >= 0 else 0)

    def set_rows(self, rows) -> None:
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row["order_number"], row["order_category"], row["specs"], row["qa"], row["dcl"],
                row["drawings"], row["fat"], row["overall_status"], row["completed_at"].replace("T", " "),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column in (2, 3, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, column, item)
        self.table.resizeColumnsToContents()
        self.count_label.setText(f"{len(rows)} order{'s' if len(rows) != 1 else ''}")
        query = self.search.text().strip()
        status = "" if self.status.currentIndex() == 0 else self.status.currentText()
        order_type = "" if self.order_type.currentIndex() == 0 else self.order_type.currentText()
        if rows:
            details = []
            if query:
                details.append(f'order containing "{query}"')
            if status:
                details.append(status)
            if order_type:
                details.append(order_type)
            if self.period.currentData() != "all":
                details.append(self.period.currentText())
            self.filter_message.setText(
                f"Showing {len(rows)} matching order{'s' if len(rows) != 1 else ''}"
                + (f" for {' and '.join(details)}." if details else ".")
            )
        else:
            if query or status or order_type:
                self.filter_message.setText("No history records match the applied filters.")
            else:
                self.filter_message.setText("No verification history has been recorded yet.")

    def _recheck(self) -> None:
        selected = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        numbers = [self.table.item(row, 0).text() for row in selected]
        if numbers:
            self.recheck_requested.emit(numbers)

    def set_event_rows(self, rows) -> None:
        self.events.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [row["so"], row["order_type"], row["event_type"], row["outcome"],
                      row["actor"] or "—", row["created_at"].replace("T", " ")]
            for column, value in enumerate(values):
                self.events.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.events.resizeColumnsToContents()

