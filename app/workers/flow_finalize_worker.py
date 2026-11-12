from PySide6.QtCore import QObject, Signal, Slot


class FlowFinalizeWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, controller, flow_run):
        super().__init__()
        self.controller = controller
        self.flow_run = flow_run

    @Slot()
    def run(self):
        try:
            self.progress.emit(10, "Loading approved Flow review rows…")
            self.progress.emit(40, "Reading live Smartsheet data…")
            self.finished.emit(self.controller.flow_final_preview(self.flow_run))
            self.progress.emit(100, "Final Flow preview ready")
        except Exception as exc:
            self.failed.emit(str(exc))
