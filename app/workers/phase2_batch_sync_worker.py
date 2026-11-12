from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from app.phase2.sync_executor import SyncExecutionResult


class Phase2BatchSyncWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, controller, runs):
        super().__init__()
        self.controller = controller
        self.runs = list(runs)
        self._cancelled = False

    @Slot()
    def cancel(self):
        self._cancelled = True

    @Slot()
    def run(self):
        total_rows = sum(len(run.plan_rows) for run in self.runs)
        completed_before = 0
        aggregate = SyncExecutionResult()
        try:
            # One authenticated gateway keeps the live sheet cached across the
            # entire mixed Project/Flow batch. Each completed job refreshes that
            # cache during its single batch readback.
            gateway = self.controller.phase2_sync_gateway()
            for run in self.runs:
                if self._cancelled:
                    break

                def report(completed, _run_total, message, base=completed_before, so=run.order.so):
                    overall = round((base + completed) * 100 / max(total_rows, 1))
                    self.progress.emit(overall, f"{so}: {message}")

                result = self.controller.phase2_sync(
                    run, confirmed=True, cancelled=lambda: self._cancelled, progress=report,
                    gateway=gateway,
                )
                for field in ("created", "updated", "unchanged", "blocked", "failed", "reconciled"):
                    setattr(aggregate, field, getattr(aggregate, field) + getattr(result, field))
                aggregate.errors.extend(f"{run.order.so}: {error}" for error in result.errors)
                completed_before += len(run.plan_rows)
            self.progress.emit(100, "Batch synchronization complete")
            self.finished.emit(aggregate)
        except Exception as exc:
            self.failed.emit(str(exc))
