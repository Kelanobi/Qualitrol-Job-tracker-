from PySide6.QtCore import QObject, Signal, Slot


class ClassificationCorrectionWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, controller, so: str):
        super().__init__()
        self.controller = controller
        self.so = so

    @Slot()
    def run(self):
        try:
            self.controller.change_project_to_flow(self.so)
            self.finished.emit(self.so)
        except Exception as exc:
            self.failed.emit(str(exc))
