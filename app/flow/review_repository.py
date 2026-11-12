from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
import sqlite3

from app.flow.models import FlowDclLine, FlowReviewItem, ReviewStatus
from app.flow.processor import FlowRun
from app.phase2.persistence import Phase2Repository


class FlowReviewRepository:
    def __init__(self, phase2_repository: Phase2Repository):
        self.repository = phase2_repository

    def save_run(self, run: FlowRun) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.repository._connect() as connection:
            cursor = connection.execute("""
                INSERT INTO phase2_runs (
                    so, classification, mode, status, started_at, completed_at, parser_version
                ) VALUES (?, 'Flow', 'review', 'needs_review', ?, ?, 'flow-1')
            """, (run.order.so, now, now))
            run_id = int(cursor.lastrowid); run.run_id = run_id
            for line, item in zip(run.lines, run.review_items):
                line_cursor = connection.execute("""
                    INSERT INTO flow_dcl_lines (
                        run_id, so, customer, source_file, source_page, line_reference,
                        part_code, description, quantity, unit, explicit_serials_json, raw_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    run_id, line.so, line.customer, line.source_file, line.page, line.line_reference,
                    line.part_code, line.description, str(line.quantity) if line.quantity is not None else None,
                    line.unit, json.dumps(line.explicit_serials), line.raw_text,
                ))
                review_cursor = connection.execute("""
                    INSERT INTO flow_review_items (
                        run_id, dcl_line_id, category, serial_pattern, proposed_serials_json,
                        represented_quantity, status, issues_json, catalogue_key,
                        catalogue_version, approved, excluded_reason, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    run_id, int(line_cursor.lastrowid), item.category, item.serial_pattern,
                    json.dumps(item.proposed_serials),
                    str(item.represented_quantity) if item.represented_quantity is not None else None,
                    item.status.value, json.dumps(item.issues), item.catalogue_key,
                    item.catalogue_version, int(item.approved), item.excluded_reason, now,
                ))
                connection.execute(
                    "UPDATE flow_review_items SET proposed_value_overrides_json=? WHERE id=?",
                    (json.dumps(item.proposed_value_overrides), int(review_cursor.lastrowid)),
                )
                review_id = int(review_cursor.lastrowid)
                connection.executemany("""
                    INSERT INTO flow_proposed_serials (review_item_id, serial, position, status)
                    VALUES (?, ?, ?, ?)
                """, [(review_id, serial, index, item.status.value)
                       for index, serial in enumerate(item.proposed_serials)])
            connection.execute("""
                INSERT INTO app_events (so, run_id, order_type, event_type, outcome, detail_json, created_at)
                VALUES (?, ?, 'Flow', 'extraction', ?, ?, ?)
            """, (run.order.so, run_id, "needs_review", json.dumps({"lines": len(run.lines)}), now))
        return run_id

    def edit_item(self, review_id: int, field_name: str, value, actor: str | None = None, reason: str | None = None) -> None:
        allowed = {"category", "serial_pattern", "proposed_serials_json", "status", "approved", "excluded_reason"}
        if field_name not in allowed:
            raise ValueError(f"Review field {field_name!r} is not editable.")
        with self.repository._connect() as connection:
            row = connection.execute(
                f"SELECT r.{field_name}, r.run_id, l.so FROM flow_review_items r "
                "JOIN flow_dcl_lines l ON l.id=r.dcl_line_id WHERE r.id=?", (review_id,)
            ).fetchone()
            if row is None:
                raise KeyError(review_id)
            before = row[0]
            stored = json.dumps(value) if field_name == "proposed_serials_json" else value
            connection.execute(
                f"UPDATE flow_review_items SET {field_name}=?, updated_at=? WHERE id=?",
                (stored, datetime.now().isoformat(timespec="seconds"), review_id),
            )
            connection.execute("""
                INSERT INTO flow_review_edits (
                    review_item_id, field_name, before_json, after_json, actor, reason, edited_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                review_id, field_name, json.dumps(before), json.dumps(stored), actor, reason,
                datetime.now().isoformat(timespec="seconds"),
            ))
            connection.execute("""
                INSERT INTO app_events (so, run_id, order_type, event_type, outcome, detail_json, actor, created_at)
                VALUES (?, ?, 'Flow', 'review_edit', 'saved', ?, ?, ?)
            """, (row["so"], row["run_id"], json.dumps({"review_id": review_id, "field": field_name}),
                  actor, datetime.now().isoformat(timespec="seconds")))

    def load_items(self, run_id: int) -> list[tuple[int, FlowReviewItem]]:
        with self.repository._connect() as connection:
            rows = connection.execute("""
                SELECT r.*, l.* FROM flow_review_items r
                JOIN flow_dcl_lines l ON l.id=r.dcl_line_id
                WHERE r.run_id=? ORDER BY r.id
            """, (run_id,)).fetchall()
        results = []
        for row in rows:
            line = FlowDclLine(
                so=row["so"], customer=row["customer"], source_file=row["source_file"],
                page=row["source_page"], line_reference=row["line_reference"],
                part_code=row["part_code"], description=row["description"],
                quantity=Decimal(row["quantity"]) if row["quantity"] is not None else None,
                unit=row["unit"], explicit_serials=json.loads(row["explicit_serials_json"]),
                raw_text=row["raw_text"],
            )
            item = FlowReviewItem(
                line=line, category=row["category"], serial_pattern=row["serial_pattern"],
                proposed_serials=json.loads(row["proposed_serials_json"]),
                represented_quantity=Decimal(row["represented_quantity"]) if row["represented_quantity"] else None,
                status=ReviewStatus(row["status"]), issues=json.loads(row["issues_json"]),
                catalogue_key=row["catalogue_key"], catalogue_version=row["catalogue_version"] or 1,
                approved=bool(row["approved"]), excluded_reason=row["excluded_reason"],
                proposed_value_overrides=json.loads(row["proposed_value_overrides_json"] or "{}"),
            )
            results.append((row["id"], item))
        return results

    def load_latest_items(self, so: str) -> list[tuple[int, FlowReviewItem]]:
        """Load the latest persisted Flow review so job switching keeps decisions."""
        with self.repository._connect() as connection:
            row = connection.execute("""
                SELECT id FROM phase2_runs
                WHERE so=? AND classification='Flow' AND mode='review'
                ORDER BY id DESC LIMIT 1
            """, (so,)).fetchone()
        return self.load_items(int(row["id"])) if row else []

    def edit_preview_value(
        self, review_id: int, serial_index: int, field: str, value: str,
        actor: str | None = None,
    ) -> None:
        from app.phase2.sync_planner import APPROVED_FIELDS
        if field not in APPROVED_FIELDS:
            raise ValueError(f"Smartsheet field {field!r} is not editable.")
        with self.repository._connect() as connection:
            row = connection.execute(
                "SELECT proposed_value_overrides_json, run_id FROM flow_review_items WHERE id=?",
                (review_id,),
            ).fetchone()
            if row is None:
                raise KeyError(review_id)
            overrides = json.loads(row["proposed_value_overrides_json"] or "{}")
            key = str(serial_index)
            values = dict(overrides.get(key) or {})
            cleaned = value.strip()
            if cleaned:
                values[field] = cleaned
            else:
                values.pop(field, None)
            if values:
                overrides[key] = values
            else:
                overrides.pop(key, None)
            now = datetime.now().isoformat(timespec="seconds")
            connection.execute(
                "UPDATE flow_review_items SET proposed_value_overrides_json=?, updated_at=? WHERE id=?",
                (json.dumps(overrides), now, review_id),
            )
            connection.execute("""
                INSERT INTO flow_review_edits (
                    review_item_id, field_name, before_json, after_json, actor, reason, edited_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
            """, (review_id, f"preview.{serial_index}.{field}", row[0], json.dumps(overrides), actor, now))
