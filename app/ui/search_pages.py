from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox, QButtonGroup, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QFrame, QProgressBar, QPushButton, QScrollArea, QSpinBox, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from app.ui.widgets import Section


class AutomaticSearchPage(QWidget):
    start_requested = Signal()
    back_requested = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 34, 40, 40)
        section = Section("Automatic Search", "Find DMS orders that have never been checked before.")
        self.count_label = QLabel("Select Find New Orders to scan the workbook.")
        self.count_label.setObjectName("centerMessage")
        self.count_label.setAlignment(Qt.AlignCenter)
        section.layout.addWidget(self.count_label)
        self.activity = QProgressBar()
        self.activity.setObjectName("searchActivity")
        self.activity.setRange(0, 0)
        self.activity.setTextVisible(False)
        self.activity.setFixedHeight(8)
        self.activity.hide()
        section.layout.addWidget(self.activity)
        buttons = QHBoxLayout()
        back = QPushButton("Back")
        back.setObjectName("secondaryButton")
        back.clicked.connect(self.back_requested)
        self.start_button = QPushButton("Find New Orders")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_requested)
        buttons.addStretch()
        buttons.addWidget(back)
        buttons.addWidget(self.start_button)
        section.layout.addLayout(buttons)
        layout.addWidget(section)
        layout.addStretch()

    def set_searching(self) -> None:
        self.start_button.setEnabled(False)
        self.activity.show()
        self.count_label.setText("Scanning the workbook and search history for new DMS orders…")

    def set_result(self, message: str) -> None:
        self.activity.hide()
        self.start_button.setEnabled(True)
        self.count_label.setText(message)

    def set_error(self, message: str) -> None:
        self.activity.hide()
        self.start_button.setEnabled(True)
        self.count_label.setText(message)


class ManualSearchPage(QWidget):
    verify_requested = Signal(list)
    back_requested = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 40)
        section = Section("Manual Search", "Enter specific DMS order numbers to verify or recheck.")
        quantity_row = QHBoxLayout()
        quantity_row.addWidget(QLabel("How many DMS orders would you like to check?"))
        quantity_control = QFrame()
        quantity_control.setObjectName("quantityControl")
        quantity_control.setFixedSize(112, 48)
        quantity_layout = QHBoxLayout(quantity_control)
        quantity_layout.setContentsMargins(0, 0, 0, 0)
        quantity_layout.setSpacing(0)
        self.quantity = QSpinBox()
        self.quantity.setObjectName("quantitySpin")
        self.quantity.setRange(1, 100)
        self.quantity.setValue(1)
        self.quantity.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.quantity.setAccelerated(True)
        self.quantity.setAlignment(Qt.AlignCenter)
        self.quantity.setFrame(False)
        self.quantity.setToolTip("Use the arrows or type a number from 1 to 100")
        self.quantity.valueChanged.connect(self._rebuild_fields)
        quantity_layout.addWidget(self.quantity, 1)
        step_buttons = QVBoxLayout()
        step_buttons.setContentsMargins(0, 0, 0, 0)
        step_buttons.setSpacing(0)
        self.quantity_up = QToolButton()
        self.quantity_up.setObjectName("quantityStep")
        self.quantity_up.setArrowType(Qt.UpArrow)
        self.quantity_up.setToolTip("Increase number of orders")
        self.quantity_up.clicked.connect(self.quantity.stepUp)
        self.quantity_down = QToolButton()
        self.quantity_down.setObjectName("quantityStep")
        self.quantity_down.setArrowType(Qt.DownArrow)
        self.quantity_down.setToolTip("Decrease number of orders")
        self.quantity_down.clicked.connect(self.quantity.stepDown)
        step_buttons.addWidget(self.quantity_up)
        step_buttons.addWidget(self.quantity_down)
        quantity_layout.addLayout(step_buttons)
        quantity_row.addWidget(quantity_control)
        quantity_row.addStretch()
        section.layout.addLayout(quantity_row)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName("inputScroll")
        self.field_container = QWidget()
        self.form = QFormLayout(self.field_container)
        self.form.setContentsMargins(8, 8, 8, 8)
        self.scroll.setWidget(self.field_container)
        section.layout.addWidget(self.scroll, 1)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        section.layout.addWidget(self.error_label)
        controls = QHBoxLayout()
        back = QPushButton("Back")
        back.setObjectName("secondaryButton")
        back.clicked.connect(self.back_requested)
        verify = QPushButton("Review & Verify")
        verify.setObjectName("primaryButton")
        verify.clicked.connect(self._submit)
        controls.addStretch()
        controls.addWidget(back)
        controls.addWidget(verify)
        section.layout.addLayout(controls)
        layout.addWidget(section, 1)
        self.fields: list[QLineEdit] = []
        self._rebuild_fields(1)

    def _rebuild_fields(self, count: int) -> None:
        existing = [field.text() for field in getattr(self, "fields", [])]
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields = []
        for index in range(count):
            field = QLineEdit(existing[index] if index < len(existing) else "")
            field.setPlaceholderText("e.g. 50577574")
            field.setClearButtonEnabled(True)
            self.form.addRow(f"Order Number {index + 1}", field)
            self.fields.append(field)

    def _submit(self) -> None:
        self.error_label.clear()
        self.verify_requested.emit([field.text() for field in self.fields])

    def show_errors(self, errors: dict[str, str]) -> None:
        self.error_label.setText("\n".join(f"• {message}" for message in errors.values()))


class ProgressPage(QWidget):
    cancel_requested = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(80, 80, 80, 80)
        section = Section("Verification in progress", "Checking the selected DMS orders.")
        self.order_label = QLabel("Preparing verification…")
        self.order_label.setObjectName("progressOrder")
        self.order_label.setAlignment(Qt.AlignCenter)
        section.layout.addWidget(self.order_label)
        self.operation_label = QLabel("Starting")
        self.operation_label.setObjectName("muted")
        self.operation_label.setAlignment(Qt.AlignCenter)
        section.layout.addWidget(self.operation_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        section.layout.addWidget(self.progress)
        self.count_label = QLabel("0 of 0 completed")
        self.count_label.setAlignment(Qt.AlignCenter)
        section.layout.addWidget(self.count_label)
        self.cancel_button = QPushButton("Cancel after current safe step")
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self._request_cancel)
        section.layout.addWidget(self.cancel_button, alignment=Qt.AlignCenter)
        layout.addWidget(section)
        layout.addStretch()

    def start_indeterminate(self, title: str, operation: str) -> None:
        self.order_label.setText(title)
        self.operation_label.setText(operation)
        self.progress.setRange(0, 0)
        self.progress.setFormat("")
        self.count_label.clear()
        self.cancel_button.setText("Cancel")
        self.cancel_button.setEnabled(True)

    def _request_cancel(self) -> None:
        self.cancel_button.setText("Cancelling safely…")
        self.cancel_button.setEnabled(False)
        self.cancel_requested.emit()

    def update_progress(self, completed: int, total: int, order: str, operation: str) -> None:
        self.cancel_button.setText("Cancel after current safe step")
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(completed)
        self.progress.setFormat(f"{round(completed * 100 / max(total, 1))}%")
        self.order_label.setText(f"Order {order}" if order else "Finalising results")
        self.operation_label.setText(operation)
        self.count_label.setText(
            f"{round(completed * 100 / max(total, 1))}% complete   •   {completed} of {total} steps"
        )

