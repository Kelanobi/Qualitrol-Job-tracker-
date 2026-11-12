import sqlite3

from app.services.database_health import ensure_healthy_database


def test_malformed_database_is_backed_up_and_quarantined(tmp_path):
    path = tmp_path / "history.db"
    path.write_bytes(b"not a sqlite database")
    backup = ensure_healthy_database(path)
    assert backup is not None and backup.exists()
    assert backup.read_bytes() == b"not a sqlite database"
    assert not path.exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
