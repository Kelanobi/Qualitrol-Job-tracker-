from __future__ import annotations

from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook


@pytest.fixture
def auth_session():
    return SimpleNamespace(
        email="kelvin.obi@qualitrolcorp.com", session_id="test-session",
        windows_username="kelvin.obi", windows_sid="S-1-test",
        device_identifier="test-device", login_timestamp=datetime.now(),
    )


@pytest.fixture
def workbook_path(tmp_path: Path) -> Path:
    path = tmp_path / "orders.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["Category", "Stream", "Order", "D", "E", "F", "G", "H", "I", "J", "Specs", "QA", "DCL", "Drawings", "Modified by"])
    sheet.append(["Flow", "DMS", "00123", "keep", None, None, None, None, None, None, None, None, None, None, "unchanged"])
    sheet.append(["Project", "DMS", 456, "keep", None, None, None, None, None, None, "Old", "Old", "Old", "Old", "unchanged"])
    sheet.append(["Project", "IP", 789, "keep", None, None, None, None, None, None, "IP", "IP", "IP", "IP", "unchanged"])
    sheet["P2"] = "=1+1"
    workbook.save(path)
    return path


def make_documents(root: Path, order: str, category: str = "Project", missing: set[str] | None = None):
    missing = missing or set()
    paths = {
        "specs": root / order / "Specs",
        "qa": root / order / "QA",
        "dcl": root / order / "DCL",
        "drawings": root / order / "Drawings",
        "fat": root / order / "FAT",
    }
    for key, path in paths.items():
        if key in {"drawings", "fat"} and category.casefold() == "flow":
            continue
        path.mkdir(parents=True, exist_ok=True)
        if key in missing:
            continue
        extension = ".xlsx" if key == "qa" else ".docx" if key == "fat" else ".pdf"
        (path / f"arbitrary{extension}").write_bytes(b"test")
    return paths

