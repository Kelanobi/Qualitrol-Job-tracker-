from datetime import datetime

import pytest

from app.phase2.models import EquipmentCandidate, Phase2DryRun, PlanAction, ProjectOrder, SyncPlanRow
from app.phase2.persistence import Phase2Repository
from app.phase2.sync_executor import SyncExecutor


class FakeGateway:
    def __init__(self):
        self.rows = {}
        self.next_id = 10
        self.create_calls = 0
        self.update_calls = 0
        self.fail_after_create = False

    def find_rows(self, so, serial):
        return [(row_id, values) for row_id, values in self.rows.items()
                if values.get("SO No.") == so and values.get("Serial No") == serial]

    def create_row(self, values):
        self.create_calls += 1
        row_id = self.next_id; self.next_id += 1
        self.rows[row_id] = dict(values)
        if self.fail_after_create:
            raise TimeoutError("unknown response")
        return row_id

    def update_row(self, row_id, values):
        self.update_calls += 1
        self.rows[row_id].update(values)

    def read_row(self, row_id):
        return dict(self.rows[row_id])


class BatchReadGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self.read_rows_calls = 0

    def read_rows(self, row_ids):
        self.read_rows_calls += 1
        return {row_id: dict(self.rows[row_id]) for row_id in row_ids}


def make_run(repository, rows):
    run = Phase2DryRun(
        order=ProjectOrder(2, "50577574", "CIP", None), documents=[],
        equipment=[EquipmentCandidate("RCC", serial="50577574-RCC")], issues=[],
        plan_rows=rows, created_at=datetime(2026, 10, 21, 10, 0),
    )
    repository.save_dry_run(run)
    return run


def proposed(serial="50577574-RCC"):
    return {"Forecast Month": "21-Oct", "Type": "Project", "SO No.": "50577574",
            "Customer": "Customer", "Incoterms": "CIP", "Serial No": serial}


def test_executor_requires_explicit_confirmation(tmp_path):
    repository = Phase2Repository(tmp_path / "db.sqlite")
    run = make_run(repository, [SyncPlanRow(PlanAction.CREATE, "RCC", "50577574-RCC", None, proposed(), {})])
    gateway = FakeGateway()
    with pytest.raises(PermissionError, match="confirmation"):
        SyncExecutor(gateway, repository).execute(run, confirmed=False)
    assert gateway.create_calls == 0


def test_create_readback_and_idempotent_rerun(tmp_path):
    repository = Phase2Repository(tmp_path / "db.sqlite")
    run = make_run(repository, [SyncPlanRow(PlanAction.CREATE, "RCC", "50577574-RCC", None, proposed(), {})])
    gateway = FakeGateway()
    first = SyncExecutor(gateway, repository).execute(run, confirmed=True)
    second = SyncExecutor(gateway, repository).execute(run, confirmed=True)
    assert first.created == 1
    assert second.unchanged == 1
    assert gateway.create_calls == 1


def test_existing_smartsheet_identity_is_never_overridden(tmp_path):
    repository = Phase2Repository(tmp_path / "db.sqlite")
    values = proposed()
    values["Forecast Month"] = "23-Oct"
    run = make_run(repository, [
        SyncPlanRow(PlanAction.UPDATE, "RCC", "50577574-RCC", 10, values, {
            "Forecast Month": ("21-Oct", "23-Oct"),
        })
    ])
    gateway = FakeGateway()
    gateway.rows[10] = proposed()
    result = SyncExecutor(gateway, repository).execute(run, confirmed=True)
    assert result.unchanged == 1
    assert gateway.update_calls == 0
    assert gateway.rows[10]["Forecast Month"] == "21-Oct"


def test_multiple_writes_use_one_authoritative_batch_readback(tmp_path):
    repository = Phase2Repository(tmp_path / "db.sqlite")
    rows = [
        SyncPlanRow(PlanAction.CREATE, "OCU", serial, None, proposed(serial), {})
        for serial in ("50577574-OCU-01", "50577574-OCU-02", "50577574-OCU-03")
    ]
    run = make_run(repository, rows)
    gateway = BatchReadGateway()
    result = SyncExecutor(gateway, repository).execute(run, confirmed=True)
    assert result.created == 3
    assert result.failed == 0
    assert gateway.read_rows_calls == 1


def test_unknown_create_response_reconciles_without_retry(tmp_path):
    repository = Phase2Repository(tmp_path / "db.sqlite")
    run = make_run(repository, [SyncPlanRow(PlanAction.CREATE, "RCC", "50577574-RCC", None, proposed(), {})])
    gateway = FakeGateway(); gateway.fail_after_create = True
    result = SyncExecutor(gateway, repository).execute(run, confirmed=True)
    assert result.reconciled == 1
    assert result.failed == 0
    assert gateway.create_calls == 1


def test_unapproved_column_is_rejected_before_gateway_write(tmp_path):
    repository = Phase2Repository(tmp_path / "db.sqlite")
    values = proposed(); values["Product"] = "never"
    run = make_run(repository, [SyncPlanRow(PlanAction.CREATE, "RCC", "50577574-RCC", None, values, {})])
    gateway = FakeGateway()
    with pytest.raises(ValueError, match="unapproved"):
        SyncExecutor(gateway, repository).execute(run, confirmed=True)
    assert gateway.create_calls == 0


def test_safe_reserved_row_is_reused_and_identity_is_preserved(tmp_path):
    repository = Phase2Repository(tmp_path / "db.sqlite")
    plan = SyncPlanRow(PlanAction.CREATE, "RCC", "50577574-RCC", None, proposed(), {},
                       placement="reuse", target_row_id=77)
    run = make_run(repository, [plan])
    gateway = FakeGateway()
    gateway.rows[77] = {"Type": "Project", "Forecast Month": "21-Oct"}
    result = SyncExecutor(gateway, repository).execute(run, confirmed=True)
    assert result.created == 1
    assert gateway.create_calls == 0
    assert gateway.rows[77]["Serial No"] == "50577574-RCC"


def test_reserved_row_is_revalidated_before_write(tmp_path):
    repository = Phase2Repository(tmp_path / "db.sqlite")
    plan = SyncPlanRow(PlanAction.CREATE, "RCC", "50577574-RCC", None, proposed(), {},
                       placement="reuse", target_row_id=77)
    run = make_run(repository, [plan])
    gateway = FakeGateway()
    gateway.rows[77] = {"Type": "Project", "Customer": "someone else's content"}
    result = SyncExecutor(gateway, repository).execute(run, confirmed=True)
    assert result.failed == 1
    assert gateway.update_calls == 0
