from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class Phase2BatchWorker(QObject):
    progress = Signal(int, int, str, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, controller, order_numbers: list[str]):
        super().__init__()
        self.controller = controller
        self.order_numbers = order_numbers
        self._cancelled = False

    @Slot()
    def cancel(self):
        self._cancelled = True

    @Slot()
    def run(self):
        prepared = []
        total = len(self.order_numbers)
        total_units = max(total * 100, 1)
        try:
            self.progress.emit(0, total_units, "", "Loading workbook classifications once")
            order_map = {item.so: item for item in self.controller.phase2_orders()}
            selected = [order_map.get(so) for so in self.order_numbers]
            missing = [so for so, order in zip(self.order_numbers, selected) if order is None]
            if missing:
                raise ValueError(f"Orders are no longer eligible for Phase 2: {', '.join(missing)}")
            snapshot = None
            if selected:
                self.progress.emit(2, total_units, "", "Loading live Smartsheet data once")
                snapshot = self.controller.phase2_live_snapshot()
            for index, so in enumerate(self.order_numbers):
                if self._cancelled:
                    self.cancelled.emit()
                    return
                base = index * 100

                def report(percent, message, job=so, offset=base):
                    self.progress.emit(offset + max(0, min(100, int(percent))), total_units, job, message)

                report(0, "Extracting and reconciling Phase 2 details")
                run, online = self.controller.phase2_preview(
                    so, progress=report, order=order_map[so], snapshot_override=snapshot,
                )
                prepared.append((run, online))
                report(100, "Phase 2 preview prepared")
            self.finished.emit(prepared)
        except Exception as exc:
            self.failed.emit(str(exc))
