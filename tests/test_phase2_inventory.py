from app.phase2.document_inventory import DocumentInventoryService


def test_inventory_uses_current_files_without_scanning_nested_archives(tmp_path):
    order = tmp_path / "50577444"
    files = {
        order / "Specs" / "PDM DCA Rev B.pdf": b"pdm",
        order / "Specs" / "nested" / "GDM DCA Rev B.pdf": b"gdm",
        order / "QA" / "Costed BOM.xlsx": b"xlsx",
        order / "DCL" / "Despatch Checklist.pdf": b"dcl",
        order / "Drawings" / "GE04870.pdf": b"drawing",
        order / "Drawings" / "notes.txt": b"ignore",
    }
    for path, contents in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)

    records = DocumentInventoryService(tmp_path, hash_limit_bytes=1024).inventory("50577444")

    assert len(records) == 4
    assert {record.detected_type for record in records} == {
        "pdm_dca", "costed_bom", "despatch_checklist", "technical_drawing"
    }
    assert all(record.sha256 for record in records if record.category != "Drawings")
    assert all(record.sha256 is None for record in records if record.category == "Drawings")
    assert {record.revision for record in records if "DCA" in record.filename} == {"B"}


def test_inventory_ignores_nested_file_when_standard_folder_has_no_current_candidate(tmp_path):
    nested = tmp_path / "50577444" / "Specs" / "legacy" / "GDM DCA Rev C.pdf"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"gdm")

    records = DocumentInventoryService(tmp_path).inventory("50577444")

    assert records == []


def test_missing_order_folder_is_not_reported_as_network_failure(tmp_path):
    assert DocumentInventoryService(tmp_path).inventory("999") == []


def test_goods_transaction_reports_are_not_parsed_as_despatch_checklists(tmp_path):
    path = tmp_path / "50577444" / "DCL" / "50577444-DCL 3rd July GTs.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"report")
    record = DocumentInventoryService(tmp_path).inventory("50577444")[0]
    assert record.detected_type == "goods_transaction_report"
