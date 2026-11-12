from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import shutil
import sqlite3


def ensure_healthy_database(path: Path) -> Path | None:
    """Quarantine a malformed local SQLite database and return its backup path."""
    path = Path(path)
    if not path.exists():
        return None
    connection = None
    try:
        connection = sqlite3.connect(path)
        result = connection.execute("PRAGMA quick_check").fetchone()
        if result and result[0] == "ok":
            return None
    except sqlite3.DatabaseError:
        pass
    finally:
        if connection is not None:
            connection.close()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}.bak")
    shutil.copy2(path, backup)
    path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            sidecar_backup = Path(f"{backup}{suffix}")
            shutil.copy2(sidecar, sidecar_backup)
            sidecar.unlink()
    return backup
