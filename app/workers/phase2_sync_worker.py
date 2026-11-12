from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class Phase2SyncWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, controller, run):
        super().__init__()
        self.controller = controller
        self.run_data = run
        self._cancelled = False

    @Slot()
    def cancel(self):
        self._cancelled = True

    @Slot()
    def run(self):
        try:
            result = self.controller.phase2_sync(
                self.run_data, confirmed=True, cancelled=lambda: self._cancelled,
                progress=lambda completed, total, message: self.progress.emit(
                    round(completed * 100 / max(total, 1)), message
                ),
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
