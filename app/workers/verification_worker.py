from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from app.controller import ApplicationController
from app.models import Order


class ManualValidationWorker(QObject):
    loaded = Signal(list, dict)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, controller: ApplicationController, numbers: list[str]):
        super().__init__()
        self.controller = controller
        self.numbers = numbers
        self._cancel_requested = False

    @Slot()
    def cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        try:
            if self._cancel_requested:
                self.cancelled.emit()
                return
            orders, errors = self.controller.validate_manual_orders(self.numbers)
            if self._cancel_requested:
                self.cancelled.emit()
                return
            self.loaded.emit(orders, errors)
        except Exception as exc:
            self.failed.emit(str(exc))


class VerificationWorker(QObject):
    progress = Signal(int, int, str, str)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, controller: ApplicationController, orders: list[Order], mode: str):
        super().__init__()
        self.controller = controller
        self.orders = orders
        self.mode = mode
        self._cancel_requested = False

    @Slot()
    def cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        results = []
        try:
            step_counts = [4 if order.category.casefold() == "project" else 3 for order in self.orders]
            total_steps = sum(step_counts) + 1  # final safe Excel persistence
            completed_steps = 0
            self.progress.emit(0, total_steps, "", "Preparing verification")
            for order, order_steps in zip(self.orders, step_counts):
                if self._cancel_requested:
                    break
                current_step = 0
                def callback(operation, o=order):
                    nonlocal current_step
                    self.progress.emit(completed_steps + current_step, total_steps, o.number, operation)
                    current_step += 1
                result = self.controller.process_order(order, self.mode, callback)
                results.append(result)
                completed_steps += order_steps
                self.progress.emit(completed_steps, total_steps, order.number, "Verification complete")
                if self._cancel_requested:
                    break
            self.progress.emit(completed_steps, total_steps, "", "Updating Excel safely")
            self.controller.persist_batch(results)
            self.progress.emit(total_steps, total_steps, "", "Complete")
            self.finished.emit(results)
        except Exception as exc:
            self.failed.emit(str(exc))


class DashboardLoadWorker(QObject):
    loaded = Signal(dict)
    failed = Signal(str)

    def __init__(
        self, controller: ApplicationController, period: str = "current_month",
        document: str = "", presence: str = "",
    ):
        super().__init__()
        self.controller = controller
        self.period = period
        self.document = document
        self.presence = presence

    @Slot()
    def run(self) -> None:
        try:
            self.loaded.emit(self.controller.dashboard_data(self.period, self.document, self.presence))
        except Exception as exc:
            self.failed.emit(str(exc))


class AutomaticCandidateWorker(QObject):
    loaded = Signal(list)
    failed = Signal(str)

    def __init__(self, controller: ApplicationController, include_all: bool = False):
        super().__init__()
        self.controller = controller
        self.include_all = include_all

    @Slot()
    def run(self) -> None:
        try:
            orders = self.controller.all_orders() if self.include_all else self.controller.automatic_candidates()
            self.loaded.emit(orders)
        except Exception as exc:
            self.failed.emit(str(exc))

