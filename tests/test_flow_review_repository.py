from decimal import Decimal

from app.flow.models import FlowDclLine, ReviewStatus
from app.flow.processor import FlowRun
from app.flow.review_repository import FlowReviewRepository
from app.flow.serial_policy import FlowSerialPolicy
from app.phase2.models import ProjectOrder
from app.phase2.persistence import Phase2Repository


def test_review_draft_survives_restart_and_edits_are_audited(tmp_path):
    db = tmp_path / "history.db"
    phase2 = Phase2Repository(db); repository = FlowReviewRepository(phase2)
    line = FlowDclLine("50565048", "Customer", "DCL.pdf", 1, "1.1", "UNKNOWN", "Unknown", Decimal("2"), "Each")
    run = FlowRun(ProjectOrder(2, "50565048", "FCA", None, "Flow"), [], [line], FlowSerialPolicy().propose([line]), [])
    run_id = repository.save_run(run)
    review_id, _ = repository.load_items(run_id)[0]
    repository.edit_item(review_id, "category", "Manual Coupler", actor="tester", reason="DCL reviewed")
    repository.edit_item(review_id, "proposed_serials_json", ["50565048-CUSTOM-01"])

    reopened = FlowReviewRepository(Phase2Repository(db))
    _, item = reopened.load_items(run_id)[0]
    assert item.category == "Manual Coupler"
    assert item.proposed_serials == ["50565048-CUSTOM-01"]
    with phase2._connect() as connection:
        edits = connection.execute("SELECT COUNT(*) FROM flow_review_edits").fetchone()[0]
    assert edits == 2


def test_latest_review_items_are_loaded_for_job_navigation(tmp_path):
    db = tmp_path / "history.db"
    repository = FlowReviewRepository(Phase2Repository(db))
    line = FlowDclLine("50565048", "Customer", "DCL.pdf", 1, "1.1", "PART", "Part", Decimal("2"), "Each")
    first = FlowRun(ProjectOrder(2, "50565048", "FCA", None, "Flow"), [], [line], FlowSerialPolicy().propose([line]), [])
    repository.save_run(first)
    second = FlowRun(ProjectOrder(2, "50565048", "FCA", None, "Flow"), [], [line], FlowSerialPolicy().propose([line]), [])
    repository.save_run(second)
    review_id, _ = repository.load_items(second.run_id)[0]
    repository.edit_item(review_id, "status", ReviewStatus.APPROVED.value, actor="tester")
    repository.edit_item(review_id, "approved", 1, actor="tester")

    latest = repository.load_latest_items("50565048")
    assert len(latest) == 1
    assert latest[0][1].status == ReviewStatus.APPROVED
    assert latest[0][1].approved is True


def test_flow_smartsheet_preview_edits_are_persisted(tmp_path):
    db = tmp_path / "history.db"
    repository = FlowReviewRepository(Phase2Repository(db))
    line = FlowDclLine("50602018", "Customer", "DCL.pdf", 1, "1.1", "PART", "Part", Decimal("105"), "Each")
    run = FlowRun(ProjectOrder(2, "50602018", "FCA", None, "Flow"), [], [line], FlowSerialPolicy().propose([line]), [])
    repository.save_run(run)
    review_id, _ = repository.load_items(run.run_id)[0]

    repository.edit_preview_value(review_id, 0, "Serial No", "50602018-PART-105", actor="tester")
    repository.edit_preview_value(review_id, 0, "Customer", "Correct Customer", actor="tester")

    _, item = repository.load_items(run.run_id)[0]
    assert item.proposed_value_overrides["0"] == {
        "Serial No": "50602018-PART-105", "Customer": "Correct Customer",
    }
