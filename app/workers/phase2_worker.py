from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class Phase2PreviewWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object, bool)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, controller, so: str):
        super().__init__()
        self.controller = controller
        self.so = so
        self._cancelled = False

    @Slot()
    def cancel(self):
        self._cancelled = True

    @Slot()
    def run(self):
        try:
            if self._cancelled:
                self.cancelled.emit()
                return
            self.progress.emit(0, f"Starting Phase 2 for {self.so}…")
            run, online = self.controller.phase2_preview(
                self.so, progress=lambda percent, message: self.progress.emit(percent, message)
            )
            if self._cancelled:
                self.cancelled.emit()
            else:
                self.progress.emit(100, f"Phase 2 preview ready for {self.so}")
                self.finished.emit(run, online)
        except Exception as exc:
            self.failed.emit(str(exc))
