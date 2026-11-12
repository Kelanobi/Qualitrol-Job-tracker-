import sqlite3
from datetime import datetime

from app.phase2.models import Phase2DryRun, ProjectOrder
from app.phase2.persistence import Phase2Repository


def test_phase2_migrations_are_additive_and_dry_run_is_saved(tmp_path):
    path = tmp_path / "history.db"
    repository = Phase2Repository(path)
    run_id = repository.save_dry_run(Phase2DryRun(
        order=ProjectOrder(row=2, so="50577444", term="CIP", workbook_customer=None),
        documents=[], equipment=[], issues=[], plan_rows=[], created_at=datetime(2026, 10, 20, 12, 0),
    ))

    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT so, mode, status FROM phase2_runs WHERE id = ?", (run_id,)).fetchone()
        tables = {item[0] for item in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert row == ("50577444", "dry-run", "planned")
    assert {
        "document_manifest", "extracted_evidence", "equipment_candidates", "validation_issues",
        "sync_plan_rows", "sync_operations", "sync_checkpoints", "phase2_decisions",
    } <= tables
    assert {"flow_dcl_lines", "flow_review_items", "flow_review_edits", "material_catalogue", "app_events"} <= tables
