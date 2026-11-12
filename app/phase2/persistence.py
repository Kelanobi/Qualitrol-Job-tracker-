from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.phase2.models import Phase2DryRun


MIGRATIONS: list[tuple[int, str]] = [
    (1, """
        CREATE TABLE IF NOT EXISTS phase2_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            so TEXT NOT NULL,
            classification TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            source_hash TEXT,
            parser_version TEXT NOT NULL,
            error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS document_manifest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES phase2_runs(id),
            category TEXT NOT NULL,
            path TEXT NOT NULL,
            filename TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified_at TEXT NOT NULL,
            sha256 TEXT,
            detected_type TEXT NOT NULL,
            revision TEXT,
            parse_status TEXT NOT NULL,
            error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS extracted_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES phase2_runs(id),
            field_name TEXT NOT NULL,
            value TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_location TEXT,
            extraction_method TEXT NOT NULL,
            confidence REAL NOT NULL,
            revision TEXT,
            validation_state TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS equipment_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES phase2_runs(id),
            equipment_type TEXT NOT NULL,
            serial TEXT,
            quantity INTEGER NOT NULL,
            colour TEXT,
            serial_source TEXT,
            blocked INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS validation_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES phase2_runs(id),
            code TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            equipment_type TEXT,
            serial TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_plan_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES phase2_runs(id),
            action TEXT NOT NULL,
            equipment_type TEXT NOT NULL,
            serial TEXT,
            existing_row_id INTEGER,
            proposed_json TEXT NOT NULL,
            changes_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES phase2_runs(id),
            operation TEXT NOT NULL,
            smartsheet_row_id INTEGER,
            request_json TEXT,
            response_json TEXT,
            outcome TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES phase2_runs(id),
            checkpoint_key TEXT NOT NULL,
            checkpoint_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, checkpoint_key)
        );
        CREATE TABLE IF NOT EXISTS phase2_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES phase2_runs(id),
            decision_key TEXT NOT NULL,
            decision_value TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            UNIQUE(run_id, decision_key)
        );
    """),
    (2, """
        CREATE TABLE IF NOT EXISTS app_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            so TEXT NOT NULL,
            run_id INTEGER,
            order_type TEXT NOT NULL,
            event_type TEXT NOT NULL,
            outcome TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}',
            actor TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_app_events_so_time ON app_events(so, created_at);
        CREATE TABLE IF NOT EXISTS flow_dcl_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES phase2_runs(id),
            so TEXT NOT NULL,
            customer TEXT,
            source_file TEXT NOT NULL,
            source_page INTEGER NOT NULL,
            line_reference TEXT,
            part_code TEXT,
            description TEXT NOT NULL,
            quantity TEXT,
            unit TEXT,
            explicit_serials_json TEXT NOT NULL DEFAULT '[]',
            raw_text TEXT NOT NULL,
            source_hash TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_flow_lines_run ON flow_dcl_lines(run_id);
        CREATE TABLE IF NOT EXISTS material_catalogue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mapping_key TEXT NOT NULL,
            version INTEGER NOT NULL,
            part_code TEXT,
            description_pattern TEXT,
            category TEXT NOT NULL,
            serial_template TEXT NOT NULL,
            constraints_json TEXT NOT NULL DEFAULT '{}',
            approved_by TEXT,
            approved_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(mapping_key, version)
        );
        CREATE TABLE IF NOT EXISTS flow_review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES phase2_runs(id),
            dcl_line_id INTEGER REFERENCES flow_dcl_lines(id),
            category TEXT,
            serial_pattern TEXT,
            proposed_serials_json TEXT NOT NULL DEFAULT '[]',
            represented_quantity TEXT,
            status TEXT NOT NULL,
            issues_json TEXT NOT NULL DEFAULT '[]',
            catalogue_key TEXT,
            catalogue_version INTEGER,
            approved INTEGER NOT NULL DEFAULT 0,
            excluded_reason TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_flow_review_run_status ON flow_review_items(run_id, status);
        CREATE TABLE IF NOT EXISTS flow_review_edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_item_id INTEGER NOT NULL REFERENCES flow_review_items(id),
            field_name TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            actor TEXT,
            reason TEXT,
            edited_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS flow_proposed_serials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_item_id INTEGER NOT NULL REFERENCES flow_review_items(id),
            serial TEXT NOT NULL,
            position INTEGER NOT NULL,
            status TEXT NOT NULL,
            UNIQUE(review_item_id, position)
        );
        CREATE INDEX IF NOT EXISTS idx_flow_serial ON flow_proposed_serials(serial);
    """),
    (3, """
        ALTER TABLE flow_review_items
        ADD COLUMN proposed_value_overrides_json TEXT NOT NULL DEFAULT '{}';
    """),
]


class Phase2Repository:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def migrate(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            for version, sql in MIGRATIONS:
                if version not in applied:
                    connection.executescript(sql)
                    connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))

    def save_dry_run(self, dry_run: Phase2DryRun, parser_version: str = "phase2-foundation-1") -> int:
        with self._connect() as connection:
            cursor = connection.execute("""
                INSERT INTO phase2_runs (
                    so, classification, mode, status, started_at, completed_at, parser_version
                ) VALUES (?, 'Project', 'dry-run', 'planned', ?, ?, ?)
            """, (
                dry_run.order.so,
                dry_run.created_at.isoformat(timespec="seconds"),
                dry_run.created_at.isoformat(timespec="seconds"),
                parser_version,
            ))
            run_id = int(cursor.lastrowid)
            dry_run.run_id = run_id
            connection.executemany("""
                INSERT INTO document_manifest (
                    run_id, category, path, filename, size, modified_at, sha256,
                    detected_type, revision, parse_status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [(
                run_id, item.category, str(item.path), item.filename, item.size,
                item.modified_at.isoformat(timespec="seconds"), item.sha256,
                item.detected_type, item.revision, item.parse_status, item.error,
            ) for item in dry_run.documents])
            all_issues = list(dry_run.issues)
            connection.executemany("""
                INSERT INTO extracted_evidence (
                    run_id, field_name, value, source_file, source_location,
                    extraction_method, confidence, revision, validation_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [(
                run_id, evidence.field, evidence.value, evidence.source_file,
                evidence.location, evidence.extraction_method, evidence.confidence,
                evidence.revision, evidence.validation_state,
            ) for evidence in dry_run.evidence])
            for candidate in dry_run.equipment:
                connection.execute("""
                    INSERT INTO equipment_candidates (
                        run_id, equipment_type, serial, quantity, colour, serial_source, blocked
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    run_id, candidate.equipment_type, candidate.serial, candidate.quantity,
                    candidate.colour, candidate.serial_source, int(candidate.blocked),
                ))
                all_issues.extend(candidate.issues)
                connection.executemany("""
                    INSERT INTO extracted_evidence (
                        run_id, field_name, value, source_file, source_location,
                        extraction_method, confidence, revision, validation_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [(
                    run_id, evidence.field, evidence.value, evidence.source_file,
                    evidence.location, evidence.extraction_method, evidence.confidence,
                    evidence.revision, evidence.validation_state,
                ) for evidence in candidate.evidence])
            connection.executemany("""
                INSERT INTO validation_issues (
                    run_id, code, severity, message, equipment_type, serial
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, [(
                run_id, issue.code, issue.severity.value, issue.message,
                issue.equipment_type, issue.serial,
            ) for issue in all_issues])
            connection.executemany("""
                INSERT INTO sync_plan_rows (
                    run_id, action, equipment_type, serial, existing_row_id,
                    proposed_json, changes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [(
                run_id, row.action.value, row.equipment_type, row.serial,
                row.existing_row_id, json.dumps(row.proposed_values), json.dumps(row.changes),
            ) for row in dry_run.plan_rows])
            return run_id

    def record_sync_operation(
        self, run_id: int, operation: str, row_id: int | None,
        request: dict, response: dict | None, outcome: str, error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO sync_operations (
                    run_id, operation, smartsheet_row_id, request_json, response_json,
                    outcome, attempted_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, operation, row_id, json.dumps(request), json.dumps(response) if response else None,
                outcome, datetime.now().isoformat(timespec="seconds"), error,
            ))

    def replace_sync_plan(self, dry_run: Phase2DryRun) -> None:
        if dry_run.run_id is None:
            raise ValueError("A persisted run is required.")
        with self._connect() as connection:
            connection.execute("DELETE FROM sync_plan_rows WHERE run_id=?", (dry_run.run_id,))
            connection.executemany("""
                INSERT INTO sync_plan_rows (
                    run_id, action, equipment_type, serial, existing_row_id,
                    proposed_json, changes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [(
                dry_run.run_id, row.action.value, row.equipment_type, row.serial,
                row.existing_row_id, json.dumps(row.proposed_values), json.dumps(row.changes),
            ) for row in dry_run.plan_rows])

    def save_checkpoint(self, run_id: int, key: str, value: dict) -> None:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO sync_checkpoints (run_id, checkpoint_key, checkpoint_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id, checkpoint_key) DO UPDATE SET
                    checkpoint_json=excluded.checkpoint_json, created_at=excluded.created_at
            """, (run_id, key, json.dumps(value), datetime.now().isoformat(timespec="seconds")))

    def complete_run(self, run_id: int, status: str, error: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute("""
                UPDATE phase2_runs SET status=?, completed_at=?, error_message=? WHERE id=?
            """, (status, datetime.now().isoformat(timespec="seconds"), error, run_id))

    def record_event(self, so: str, run_id: int | None, order_type: str, event_type: str,
                     outcome: str, detail: dict | None = None, actor: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO app_events (so, run_id, order_type, event_type, outcome, detail_json, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (so, run_id, order_type, event_type, outcome, json.dumps(detail or {}), actor,
                  datetime.now().isoformat(timespec="seconds")))

    def events(self, query: str = "", limit: int = 250):
        with self._connect() as connection:
            return list(connection.execute("""
                SELECT * FROM app_events WHERE so LIKE ? ORDER BY created_at DESC, id DESC LIMIT ?
            """, (f"%{query.strip()}%", limit)))

