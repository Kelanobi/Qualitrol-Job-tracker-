from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from contextlib import contextmanager
from pathlib import Path

from app.models import VerificationResult


class HistoryService:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_number TEXT NOT NULL,
                    order_category TEXT NOT NULL,
                    search_mode TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    specs TEXT NOT NULL,
                    qa TEXT NOT NULL,
                    dcl TEXT NOT NULL,
                    drawings TEXT NOT NULL,
                    fat TEXT NOT NULL DEFAULT 'N/A',
                    overall_status TEXT NOT NULL,
                    excel_update_status TEXT NOT NULL,
                    error_category TEXT,
                    error_message TEXT,
                    paths_json TEXT NOT NULL DEFAULT '{}'
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_attempt_order ON attempts(order_number)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_attempt_completed ON attempts(completed_at DESC)")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(attempts)")}
            if "employee_email" not in columns:
                connection.execute("ALTER TABLE attempts ADD COLUMN employee_email TEXT")
            if "session_id" not in columns:
                connection.execute("ALTER TABLE attempts ADD COLUMN session_id TEXT")
            if "fat" not in columns:
                connection.execute("ALTER TABLE attempts ADD COLUMN fat TEXT NOT NULL DEFAULT 'N/A'")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS login_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    login_timestamp TEXT NOT NULL,
                    employee_email TEXT,
                    windows_username TEXT NOT NULL,
                    windows_sid TEXT,
                    device_identifier TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    failure_reason TEXT,
                    session_id TEXT,
                    logout_timestamp TEXT
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_login_session ON login_events(session_id)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS excel_modification_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    employee_email TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    order_number TEXT NOT NULL,
                    excel_row INTEGER,
                    columns_json TEXT NOT NULL,
                    previous_values_json TEXT NOT NULL,
                    new_values_json TEXT NOT NULL,
                    workbook_saved INTEGER NOT NULL,
                    error_details TEXT
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_excel_event_order ON excel_modification_events(order_number)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_resets (
                    month_key TEXT PRIMARY KEY,
                    reset_at TEXT NOT NULL
                )
            """)

    def record(self, result: VerificationResult, session=None) -> int:
        completed = result.completed_at or result.started_at
        with self._connect() as connection:
            cursor = connection.execute("""
                INSERT INTO attempts (
                    order_number, order_category, search_mode, started_at, completed_at,
                    specs, qa, dcl, drawings, fat, overall_status, excel_update_status,
                    error_category, error_message, paths_json, employee_email, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.order.number, result.order.category, result.search_mode,
                result.started_at.isoformat(timespec="seconds"), completed.isoformat(timespec="seconds"),
                result.specs, result.qa, result.dcl, result.drawings, result.fat, result.overall_status,
                result.excel_update_status, result.error_category, "\n".join(result.errors) or None,
                json.dumps({key: str(value) for key, value in result.paths.items()}),
                session.email if session else None, session.session_id if session else None,
            ))
            return int(cursor.lastrowid)

    def record_login(
        self, timestamp: datetime, email: str, windows_username: str, windows_sid: str | None,
        device_identifier: str, success: bool, failure_reason: str | None, session_id: str | None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute("""
                INSERT INTO login_events (
                    login_timestamp, employee_email, windows_username, windows_sid,
                    device_identifier, success, failure_reason, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp.isoformat(timespec="seconds"), email or None, windows_username,
                windows_sid, device_identifier, int(success), failure_reason, session_id,
            ))
            return int(cursor.lastrowid)

    def record_logout(self, session_id: str, timestamp: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE login_events SET logout_timestamp=? WHERE session_id=? AND success=1",
                (timestamp.isoformat(timespec="seconds"), session_id),
            )

    def record_excel_modification(
        self, session, order_number: str, excel_row: int | None, columns: list[int],
        previous_values: dict[int, object], new_values: dict[int, object],
        workbook_saved: bool, error_details: str | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute("""
                INSERT INTO excel_modification_events (
                    timestamp, employee_email, session_id, order_number, excel_row,
                    columns_json, previous_values_json, new_values_json,
                    workbook_saved, error_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(timespec="seconds"), session.email, session.session_id,
                order_number, excel_row, json.dumps(columns),
                json.dumps({str(key): value for key, value in previous_values.items()}),
                json.dumps({str(key): value for key, value in new_values.items()}),
                int(workbook_saved), error_details,
            ))
            return int(cursor.lastrowid)

    def searched_order_numbers(self) -> set[str]:
        with self._connect() as connection:
            return {row[0] for row in connection.execute("SELECT DISTINCT order_number FROM attempts")}

    def latest(
        self, limit: int | None = None, status: str | None = None, query: str = "",
        order_category: str | None = None, period: str = "all",
        document: str = "", presence: str = "", after: datetime | None = None,
    ) -> list[dict[str, object]]:
        if status == "Fully Verified":
            status = "Completed Pass"
        sql = """
            SELECT a.* FROM attempts a
            JOIN (SELECT order_number, MAX(id) id FROM attempts GROUP BY order_number) latest
              ON latest.id = a.id
            WHERE a.order_number LIKE ?
        """
        params: list[object] = [f"%{query.strip()}%"]
        if order_category:
            sql += " AND a.order_category = ?"
            params.append(order_category)
        sql += " ORDER BY a.completed_at DESC, a.id DESC"
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(sql, params)]
        start, end = self.period_bounds(period)
        filtered = []
        for row in rows:
            completed = datetime.fromisoformat(str(row["completed_at"]))
            if start and completed < start:
                continue
            if end and completed >= end:
                continue
            if after and completed < after:
                continue
            effective = self.effective_status(row)
            row["overall_status"] = effective
            if status and effective != status:
                continue
            if document:
                value = str(row.get(document.casefold()) or "")
                has_document = value.casefold() == "yes"
                if presence == "has" and not has_document:
                    continue
                if presence == "missing" and has_document:
                    continue
            filtered.append(row)
            if limit is not None and len(filtered) >= limit:
                break
        return filtered

    @staticmethod
    def effective_status(row) -> str:
        required = ["specs", "qa", "dcl"]
        if str(row.get("order_category") or "").casefold() == "project":
            required.extend(["drawings", "fat"])
        return "Completed Pass" if all(
            str(row.get(field) or "").casefold() == "yes" for field in required
        ) else "Missing Documents"

    @staticmethod
    def period_bounds(period: str, now: datetime | None = None):
        now = now or datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = today.replace(day=1)
        if month_start.month == 12:
            next_month = month_start.replace(year=month_start.year + 1, month=1)
        else:
            next_month = month_start.replace(month=month_start.month + 1)
        week_start = today - timedelta(days=today.weekday())
        bounds = {
            "current_month": (month_start, next_month),
            "previous_month": (
                (month_start - timedelta(days=1)).replace(day=1), month_start,
            ),
            "current_week": (week_start, week_start + timedelta(days=7)),
            "previous_week": (week_start - timedelta(days=7), week_start),
            "all": (None, None),
        }
        return bounds.get(period, bounds["all"])

    def reset_current_month(self, when: datetime | None = None) -> datetime:
        when = when or datetime.now()
        key = when.strftime("%Y-%m")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO dashboard_resets(month_key, reset_at) VALUES (?, ?) "
                "ON CONFLICT(month_key) DO UPDATE SET reset_at=excluded.reset_at",
                (key, when.isoformat(timespec="seconds")),
            )
        return when

    def current_month_reset(self, when: datetime | None = None) -> datetime | None:
        when = when or datetime.now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT reset_at FROM dashboard_resets WHERE month_key=?",
                (when.strftime("%Y-%m"),),
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def attempts_for_order(self, order_number: str) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return list(connection.execute(
                "SELECT * FROM attempts WHERE order_number = ? ORDER BY id DESC", (order_number,)
            ))

    def month_rows(self, year: int, month: int) -> list[dict[str, object]]:
        start = datetime(year, month, 1)
        end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT a.* FROM attempts a
                JOIN (
                    SELECT order_number, MAX(id) id FROM attempts
                    WHERE completed_at >= ? AND completed_at < ? GROUP BY order_number
                ) latest ON latest.id = a.id
                ORDER BY a.completed_at, a.order_number
            """, (start.isoformat(), end.isoformat())).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            row["overall_status"] = self.effective_status(row)
        return result

