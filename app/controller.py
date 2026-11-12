from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from app.models import Order, VerificationResult
from app.services.document_verifier import DocumentVerifier, NetworkUnavailableError
from app.services.excel_service import ExcelService, WorkbookError
from app.services.history_service import HistoryService
from app.config import APPROVED_USERNAME_MAPPINGS, SMARTSHEET_SHEET_ID
from app.phase2.credentials import SecureCredentialStore
from app.phase2.persistence import Phase2Repository
from app.phase2.service import Phase2DryRunService
from app.phase2.smartsheet_client import (
    OfficialSdkSchemaManager, OfficialSdkSheetReader, OfficialSdkSyncGateway, ReadOnlySmartsheetClient,
    missing_picklist_options, validate_plan_against_live_schema,
)
from app.phase2.sync_executor import SyncExecutor
from app.flow.processor import FlowProcessor
from app.flow.review_repository import FlowReviewRepository
from app.flow.review_service import FlowReviewService
from app.flow.planner import FlowFinalPlanner
from app.flow.catalogue import MaterialCatalogue
from app.phase2.sheet_layout import assign_create_placements
from app.services.database_health import ensure_healthy_database
from app.auth import AuthenticationService
from app.services.monthly_report import MonthlyReportService


class ApplicationController:
    _SNAPSHOT_NOT_SUPPLIED = object()
    def __init__(self, workbook: Path, document_root: Path, database: Path):
        recovered = ensure_healthy_database(database)
        self.session = None
        self.history = HistoryService(database)
        self.auth = AuthenticationService(self.history, approved_mappings=APPROVED_USERNAME_MAPPINGS)
        self.excel = ExcelService(
            workbook,
            session_provider=lambda: self.session,
            audit_recorder=self.history.record_excel_modification,
        )
        self.verifier = DocumentVerifier(document_root)
        self.phase2 = Phase2DryRunService(workbook, document_root)
        self.phase2_repository = Phase2Repository(database)
        self.flow = FlowProcessor(workbook, document_root)
        self.flow_reviews = FlowReviewRepository(self.phase2_repository)
        self.monthly_reports = MonthlyReportService(
            self.history, orders_provider=self.excel.read_orders,
        )
        self.log = logging.getLogger(__name__)
        if recovered:
            self.log.error("Malformed local database was quarantined at %s", recovered)

    def authenticate(self, email: str, password: str):
        self.session = self.auth.authenticate(email, password)
        return self.session

    def sign_out(self) -> None:
        if self.session is not None:
            self.auth.logout(self.session)
            self.session = None

    def require_session(self):
        if self.session is None:
            raise RuntimeError("An authenticated employee session is required for this operation.")
        return self.session

    def has_smartsheet_token(self) -> bool:
        return bool(SecureCredentialStore().get_smartsheet_token())

    def configure_smartsheet_token(self, token: str) -> None:
        """Validate a token against the configured sheet before storing it securely."""
        cleaned = token.strip()
        if not cleaned:
            raise ValueError("Enter a Smartsheet API token.")
        reader = OfficialSdkSheetReader(cleaned)
        ReadOnlySmartsheetClient(SMARTSHEET_SHEET_ID, reader).snapshot()
        SecureCredentialStore().set_smartsheet_token(cleaned)

    def phase2_orders(self):
        # Project and Flow share one workbook schema, so read the network file
        # once instead of reopening it independently for each classification.
        return sorted(self.phase2.eligibility.read_eligible_orders(), key=lambda item: item.so)

    def phase2_live_snapshot(self, progress=None):
        progress = progress or (lambda _percent, _message: None)
        token = SecureCredentialStore().get_smartsheet_token()
        if not token:
            return None
        progress(12, "Checking the live Smartsheet schema…")
        OfficialSdkSchemaManager(SMARTSHEET_SHEET_ID, token).ensure_picklist_option(
            "Forecast Month", date.today().strftime("%d-%b")
        )
        progress(16, "Loading the live Smartsheet once…")
        return ReadOnlySmartsheetClient(
            SMARTSHEET_SHEET_ID, OfficialSdkSheetReader(token)
        ).snapshot()

    def _ensure_plan_dropdown_options(self, plan_rows, snapshot, progress=None):
        """Create extracted dropdown values before validating the final preview."""
        missing = missing_picklist_options(plan_rows, snapshot)
        if not missing:
            return snapshot
        progress = progress or (lambda _percent, _message: None)
        progress(92, "Adding newly detected values to Smartsheet dropdowns…")
        token = SecureCredentialStore().get_smartsheet_token()
        if not token:
            raise RuntimeError("A Smartsheet token is required to update dropdown options.")
        OfficialSdkSchemaManager(SMARTSHEET_SHEET_ID, token).ensure_picklist_options(missing)
        return ReadOnlySmartsheetClient(
            SMARTSHEET_SHEET_ID, OfficialSdkSheetReader(token)
        ).snapshot()

    def phase2_preview(
        self, so: str, progress=None, order=None, snapshot_override=_SNAPSHOT_NOT_SUPPLIED,
    ):
        self.require_session()
        progress = progress or (lambda _percent, _message: None)
        progress(5, f"Resolving workbook classification for {so}…")
        if order is None:
            order = next((item for item in self.phase2_orders() if item.so == so), None)
        if order is None:
            raise ValueError(f"Order {so} is not an eligible Project or Flow order.")
        if order.category == "Flow":
            progress(25, f"Reading Flow DCL for {so}…")
            run = self.flow.prepare(so, order=order)
            progress(80, "Saving Flow review rows…")
            self.flow_reviews.save_run(run)
            run.review_records = self.flow_reviews.load_items(run.run_id)
            # Recognition generates a proposal; it is not user approval. A
            # newly prepared test always starts clean and requires an explicit
            # Approve action before any Flow row can be synchronized.
            progress(100, "Flow review ready")
            return run, False
        snapshot = (
            self.phase2_live_snapshot(progress)
            if snapshot_override is self._SNAPSHOT_NOT_SUPPLIED
            else snapshot_override
        )
        run = self.phase2.prepare(
            so, live_rows=snapshot.rows if snapshot else None, progress=progress, order=order,
        )
        if snapshot:
            snapshot = self._ensure_plan_dropdown_options(run.plan_rows, snapshot, progress)
            validate_plan_against_live_schema(run.plan_rows, snapshot)
            assign_create_placements(run.plan_rows, snapshot.rows, "Project")
        self.phase2_repository.save_dry_run(run)
        progress(100, "Project Smartsheet preview ready")
        return run, snapshot is not None

    def flow_review_edit(self, review_id: int, field: str, value, reason: str | None = None):
        session = self.require_session()
        self.flow_reviews.edit_item(review_id, field, value, actor=session.email, reason=reason)

    def flow_preview_edit(self, review_id: int, serial_index: int, field: str, value: str) -> None:
        """Persist an editable Flow Smartsheet-preview cell on its review row."""
        session = self.require_session()
        self.flow_reviews.edit_preview_value(
            review_id, serial_index, field, value, actor=session.email,
        )

    def add_flow_prefix(self, part_number: str, prefix: str) -> tuple[str, str]:
        """Persist one shared fallback mapping without overriding approved nomenclature."""
        self.require_session()
        builtin = MaterialCatalogue().builtin_match(part_number)
        if builtin is not None:
            raise ValueError(
                f"{part_number.strip().upper()} already uses the saved {builtin.category} nomenclature. "
                "The existing rule remains unchanged."
            )
        return self.flow.prefixes.add_or_update(part_number, prefix)

    def flow_review_approve(self, run_id: int, review_ids: set[int]):
        session = self.require_session()
        items = self.flow_reviews.load_items(run_id)
        errors = FlowReviewService.approve(items, review_ids)
        for review_id, item in items:
            if review_id in review_ids and review_id not in errors:
                self.flow_reviews.edit_item(review_id, "status", item.status.value, actor=session.email)
                self.flow_reviews.edit_item(review_id, "approved", 1, actor=session.email)
        return errors, self.flow_reviews.load_items(run_id)

    def flow_review_exclude(self, review_ids: set[int], reason: str):
        session = self.require_session()
        for review_id in review_ids:
            self.flow_reviews.edit_item(review_id, "status", "excluded", actor=session.email, reason=reason)
            self.flow_reviews.edit_item(review_id, "excluded_reason", reason, actor=session.email, reason=reason)

    def flow_final_preview(self, flow_run, snapshot=None):
        token = SecureCredentialStore().get_smartsheet_token()
        if not token:
            raise RuntimeError("A Smartsheet token is required for the final Flow preview.")
        today = date.today()
        if snapshot is None:
            OfficialSdkSchemaManager(SMARTSHEET_SHEET_ID, token).ensure_picklist_option(
                "Forecast Month", today.strftime("%d-%b")
            )
            snapshot = ReadOnlySmartsheetClient(
                SMARTSHEET_SHEET_ID, OfficialSdkSheetReader(token)
            ).snapshot()
        records = self.flow_reviews.load_items(flow_run.run_id)
        preview = FlowFinalPlanner().build(flow_run, records, snapshot.rows, today)
        snapshot = self._ensure_plan_dropdown_options(preview.plan_rows, snapshot)
        validate_plan_against_live_schema(preview.plan_rows, snapshot)
        assign_create_placements(preview.plan_rows, snapshot.rows, "Flow")
        self.phase2_repository.replace_sync_plan(preview)
        return preview

    def phase2_sync_gateway(self):
        self.require_session()
        token = SecureCredentialStore().get_smartsheet_token()
        if not token:
            raise RuntimeError("No Smartsheet credential is available.")
        snapshot = ReadOnlySmartsheetClient(
            SMARTSHEET_SHEET_ID, OfficialSdkSheetReader(token)
        ).snapshot()
        return OfficialSdkSyncGateway(SMARTSHEET_SHEET_ID, token, initial_snapshot=snapshot)

    def phase2_sync(
        self, run, confirmed: bool, cancelled=lambda: False, progress=None, gateway=None,
    ):
        session = self.require_session()
        token = SecureCredentialStore().get_smartsheet_token()
        if not token:
            raise RuntimeError("No Smartsheet credential is available.")
        # A preview may sit open, or several batch previews may reserve the same
        # blank rows. Re-read live state immediately before each job and allocate
        # only rows that are still safe, preventing stale reservations from
        # failing after an earlier job consumes them.
        gateway = gateway or self.phase2_sync_gateway()
        live_snapshot = gateway.snapshot()
        live_snapshot = self._ensure_plan_dropdown_options(run.plan_rows, live_snapshot)
        for plan in run.plan_rows:
            plan.placement = None
            plan.target_row_id = None
            plan.boundary_row_id = None
        assign_create_placements(run.plan_rows, live_snapshot.rows, run.order.category)
        # Persist the final user-edited preview, not only the originally extracted
        # proposal, so the audit trail exactly matches the submitted payload.
        self.phase2_repository.replace_sync_plan(run)
        return SyncExecutor(gateway, self.phase2_repository).execute(
            run, confirmed, cancelled, progress=progress, actor=session.email,
        )

    def dashboard_data(
        self, period: str = "current_month", document: str = "", presence: str = "",
    ) -> dict[str, object]:
        cutoff = self.history.current_month_reset() if period == "current_month" else None
        latest = self.history.latest(
            period="all" if period == "current_month" and cutoff is None else period,
            document=document, presence=presence, after=cutoff,
        )
        if period == "current_month" and cutoff is None:
            by_number = {str(row["order_number"]): row for row in latest}
            current_rows = []
            for order in self.excel.read_orders():
                row = dict(by_number.get(order.number) or {
                    "order_number": order.number, "specs": "Missing", "qa": "Missing",
                    "dcl": "Missing", "drawings": "Missing", "fat": "Missing",
                    "overall_status": "Missing Documents", "completed_at": "Not checked",
                    "employee_email": None, "error_message": None, "paths_json": "{}",
                })
                row["order_category"] = order.category
                if order.category.casefold() == "flow":
                    row["drawings"] = row["fat"] = "N/A"
                row["overall_status"] = self.history.effective_status(row)
                if document:
                    has_document = str(row[document]).casefold() == "yes"
                    if presence == "has" and not has_document:
                        continue
                    if presence == "missing" and has_document:
                        continue
                current_rows.append(row)
            latest = current_rows
        completed = sum(row["overall_status"] == "Completed Pass" for row in latest)
        return {
            "total": len(latest),
            "completed": completed,
            "missing": len(latest) - completed,
            "recent": latest,
        }

    def reset_dashboard_month(self) -> None:
        self.history.reset_current_month()

    def generate_monthly_report(self, previous: bool = False) -> Path:
        target = datetime.now()
        if previous:
            target = target.replace(day=1) - timedelta(days=1)
        return self.monthly_reports.generate(target.year, target.month)

    def phase2_events(self, query: str = ""):
        return self.phase2_repository.events(query)

    def change_project_to_flow(self, so: str) -> None:
        session = self.require_session()
        self.excel.change_order_category(so, "Project", "Flow")
        self.phase2_repository.record_event(
            so, None, "Flow", "classification_change", "completed",
            {"before": "Project", "after": "Flow", "source": "phase2_user_confirmation",
             "employee_email": session.email, "session_id": session.session_id}, actor=session.email,
        )

    def automatic_candidates(self) -> list[Order]:
        searched = self.history.searched_order_numbers()
        return [order for order in self.excel.read_orders() if order.number not in searched]

    def all_orders(self) -> list[Order]:
        """Return every current workbook order for an explicit recheck-all run."""
        return self.excel.read_orders()

    def validate_manual_orders(self, numbers: list[str]) -> tuple[list[Order], dict[str, str]]:
        cleaned = [number.strip() for number in numbers]
        errors: dict[str, str] = {}
        seen: set[str] = set()
        valid_input: list[str] = []
        for index, number in enumerate(cleaned, 1):
            if not number:
                continue
            if number in seen:
                errors[f"duplicate-{index}"] = f"Order {number} was entered more than once and was included only once."
                continue
            seen.add(number)
            valid_input.append(number)
        if not valid_input:
            return [], {"empty": "Enter at least one DMS order number."}
        found, workbook_errors = self.excel.find_orders(valid_input)
        errors.update(workbook_errors)
        return found, errors

    def process_order(self, order: Order, mode: str, progress=None) -> VerificationResult:
        try:
            result = self.verifier.verify(order, mode, progress)
        except NetworkUnavailableError as exc:
            result = VerificationResult(order=order, search_mode=mode, started_at=datetime.now())
            result.completed_at = datetime.now()
            result.errors.append(str(exc))
            result.error_category = "Network error"
        return result

    def persist_batch(self, results: list[VerificationResult]) -> None:
        session = self.require_session()
        try:
            self.excel.update_results(results)
        except WorkbookError as exc:
            for result in results:
                result.excel_update_status = "Failed"
                result.errors.append(str(exc))
                result.error_category = result.error_category or "Excel update error"
        else:
            for result in results:
                result.excel_update_status = "Saved"
        finally:
            for result in results:
                self.history.record(result, session)

