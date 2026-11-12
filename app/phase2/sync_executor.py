from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.phase2.models import Phase2DryRun, PlanAction
from app.phase2.persistence import Phase2Repository
from app.phase2.sync_planner import enforce_write_allowlist
from app.phase2.models import LiveSheetRow
from app.phase2.sheet_layout import is_safe_reserved_row


class SyncGateway(Protocol):
    def find_rows(self, so: str, serial: str) -> list[tuple[int, dict[str, str | None]]]: ...
    def create_row(self, values: dict[str, str], placement: str | None = None, boundary_row_id: int | None = None) -> int: ...
    def update_row(self, row_id: int, values: dict[str, str]) -> None: ...
    def read_row(self, row_id: int) -> dict[str, str | None]: ...


@dataclass
class SyncExecutionResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    blocked: int = 0
    failed: int = 0
    reconciled: int = 0
    errors: list[str] = field(default_factory=list)


class SyncExecutor:
    def __init__(self, gateway: SyncGateway, repository: Phase2Repository):
        self.gateway = gateway
        self.repository = repository

    def execute(
        self, run: Phase2DryRun, confirmed: bool, cancelled=lambda: False, progress=None, actor: str | None = None,
    ) -> SyncExecutionResult:
        if not confirmed:
            raise PermissionError("Explicit user confirmation is required before Smartsheet synchronization.")
        if run.run_id is None:
            raise ValueError("The dry-run must be persisted before synchronization.")
        result = SyncExecutionResult()
        progress = progress or (lambda _completed, _total, _message: None)
        total = len(run.plan_rows)
        deferred_readback = hasattr(self.gateway, "read_rows")
        pending_verification: list[tuple[int, str, int, dict[str, str], int]] = []
        progress(0, total, "Preparing approved Smartsheet changes…")
        for index, plan in enumerate(run.plan_rows):
            if cancelled():
                self.repository.save_checkpoint(run.run_id, "cancelled", {"next_index": index})
                self.repository.complete_run(run.run_id, "cancelled")
                return result
            if not plan.writable:
                result.blocked += 1
                progress(index + 1, total, f"Skipped {plan.serial or plan.equipment_type}")
                continue
            enforce_write_allowlist(plan.proposed_values)
            serial = plan.serial or ""
            live_matches = self.gateway.find_rows(run.order.so, serial)
            if len(live_matches) > 1:
                result.blocked += 1
                result.errors.append(f"Duplicate live rows appeared for {run.order.so} / {serial}.")
                continue
            if live_matches:
                # Hard safety boundary: production Smartsheet is append-only.
                # Never patch an existing identity, even if a stale preview or
                # manually edited plan requests an update.
                result.unchanged += 1
                progress(index + 1, total, f"Existing row preserved for {serial or plan.equipment_type}")
                continue
            operation = "create" if not live_matches else "update"
            row_id = live_matches[0][0] if live_matches else None
            request = dict(plan.proposed_values)
            try:
                if row_id is None:
                    if plan.target_row_id is not None:
                        current = (
                            self.gateway.cached_row(plan.target_row_id)
                            if hasattr(self.gateway, "cached_row") else None
                        ) or self.gateway.read_row(plan.target_row_id)
                        if not is_safe_reserved_row(LiveSheetRow(plan.target_row_id, current)):
                            raise RuntimeError(f"Reserved row {plan.target_row_id} is no longer safe to reuse.")
                        row_id = plan.target_row_id
                        self.gateway.update_row(row_id, request)
                        operation = "reuse_reserved"
                    elif plan.placement:
                        row_id = self.gateway.create_row(request, plan.placement, plan.boundary_row_id)
                    else:
                        row_id = self.gateway.create_row(request)
                    result.created += 1
                else:
                    current = live_matches[0][1]
                    patch = {key: value for key, value in request.items() if str(current.get(key) or "").strip() != value}
                    if not patch:
                        result.unchanged += 1
                        progress(index + 1, total, f"No change for {plan.serial or plan.equipment_type}")
                        continue
                    enforce_write_allowlist(patch)
                    self.gateway.update_row(row_id, patch)
                    request = patch
                    result.updated += 1
                if deferred_readback:
                    pending_verification.append((row_id, operation, index, request, index))
                else:
                    readback = self.gateway.read_row(row_id)
                    mismatches = {key: value for key, value in request.items() if str(readback.get(key) or "").strip() != value}
                    if mismatches:
                        raise RuntimeError(f"Readback mismatch for fields: {sorted(mismatches)}")
                    self.repository.record_sync_operation(
                        run.run_id, operation, row_id, request, {key: readback.get(key) for key in request}, "verified",
                    )
                    self.repository.save_checkpoint(run.run_id, f"row-{index}", {"row_id": row_id, "outcome": "verified"})
            except Exception as exc:
                # A create may have succeeded even if its response was lost. Reconcile
                # identity before deciding whether retry is safe.
                matches_after = self.gateway.find_rows(run.order.so, serial)
                if operation == "create" and len(matches_after) == 1:
                    row_id = matches_after[0][0]
                    result.reconciled += 1
                    self.repository.record_sync_operation(
                        run.run_id, operation, row_id, request, None, "reconciled_after_unknown", str(exc),
                    )
                else:
                    result.failed += 1
                    result.errors.append(f"{serial}: {exc}")
                    self.repository.record_sync_operation(
                        run.run_id, operation, row_id, request, None, "failed", str(exc),
                    )
                self.repository.save_checkpoint(run.run_id, f"row-{index}", {"row_id": row_id, "outcome": "failed"})
            progress(index + 1, total, f"Verified {plan.serial or plan.equipment_type}")
        if pending_verification:
            progress(total, total, "Verifying all submitted rows in one readback…")
            try:
                verified_rows = self.gateway.read_rows([item[0] for item in pending_verification])
            except Exception as exc:
                verified_rows = {}
                batch_error = str(exc)
            else:
                batch_error = "Row was not returned by the final Smartsheet readback."
            for row_id, operation, index, request, _ in pending_verification:
                readback = verified_rows.get(row_id)
                mismatches = (
                    list(request) if readback is None else
                    [key for key, value in request.items() if str(readback.get(key) or "").strip() != value]
                )
                if mismatches:
                    result.failed += 1
                    message = batch_error if readback is None else f"Readback mismatch for fields: {sorted(mismatches)}"
                    result.errors.append(f"{request.get('Serial No', row_id)}: {message}")
                    self.repository.record_sync_operation(
                        run.run_id, operation, row_id, request, readback, "failed", message,
                    )
                    outcome = "failed"
                else:
                    self.repository.record_sync_operation(
                        run.run_id, operation, row_id, request,
                        {key: readback.get(key) for key in request}, "verified",
                    )
                    outcome = "verified"
                self.repository.save_checkpoint(run.run_id, f"row-{index}", {"row_id": row_id, "outcome": outcome})
        status = "completed" if not result.failed else "partial_failure"
        self.repository.complete_run(run.run_id, status, "\n".join(result.errors) or None)
        self.repository.record_event(
            run.order.so, run.run_id, run.order.category, "smartsheet_sync", status,
            {"created": result.created, "updated": result.updated, "unchanged": result.unchanged,
             "blocked": result.blocked, "failed": result.failed, "reconciled": result.reconciled}, actor=actor,
        )
        return result
