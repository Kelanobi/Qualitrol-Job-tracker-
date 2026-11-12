from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Order
from app.services.document_verifier import DocumentVerifier, NetworkUnavailableError
from tests.conftest import make_documents


def order(number="100", category="Project"):
    return Order(2, category, "DMS", number)


def test_project_all_documents_yes(tmp_path):
    make_documents(tmp_path, "100")
    result = DocumentVerifier(tmp_path).verify(order(), "Manual")
    assert result.document_values == ("Yes", "Yes", "Yes", "Yes", "Yes")
    assert result.overall_status == "Completed Pass"


def test_flow_drawings_are_na_and_not_searched(tmp_path):
    make_documents(tmp_path, "100", "Flow")
    result = DocumentVerifier(tmp_path).verify(order(category="Flow"), "Manual")
    assert result.document_values == ("Yes", "Yes", "Yes", "N/A", "N/A")
    assert "drawings" not in result.paths
    assert "fat" not in result.paths


@pytest.mark.parametrize("key", ["specs", "qa", "dcl", "drawings", "fat"])
def test_empty_required_folder_is_missing(tmp_path, key):
    make_documents(tmp_path, "100", missing={key})
    result = DocumentVerifier(tmp_path).verify(order(), "Manual")
    assert getattr(result, key) == "Missing"
    assert result.overall_status == "Missing Documents"


def test_extension_matching_is_case_insensitive(tmp_path):
    paths = make_documents(tmp_path, "100", missing={"specs", "qa", "dcl", "drawings", "fat"})
    (paths["specs"] / "SPEC.PDF").write_bytes(b"x")
    (paths["qa"] / "anything.XLSX").write_bytes(b"x")
    (paths["dcl"] / "list.PdF").write_bytes(b"x")
    (paths["drawings"] / "one.PDF").write_bytes(b"x")
    (paths["fat"] / "procedure.DOCX").write_bytes(b"not a real Word file")
    result = DocumentVerifier(tmp_path).verify(order(), "Manual")
    assert result.document_values == ("Yes", "Yes", "Yes", "Yes", "Yes")


def test_fat_is_presence_only_and_accepts_legacy_doc(tmp_path):
    paths = make_documents(tmp_path, "100", missing={"fat"})
    (paths["fat"] / "FAT Procedure.doc").write_bytes(b"content must never be parsed")
    assert DocumentVerifier(tmp_path).verify(order(), "Manual").fat == "Yes"


def test_nested_files_are_ignored(tmp_path):
    paths = make_documents(tmp_path, "100", missing={"specs"})
    nested = paths["specs"] / "nested"
    nested.mkdir()
    (nested / "spec.pdf").write_bytes(b"x")
    assert DocumentVerifier(tmp_path).verify(order(), "Manual").specs == "Missing"


@pytest.mark.parametrize("key,extension", [
    ("specs", ".pdf"), ("qa", ".xlsx"), ("dcl", ".pdf"),
    ("drawings", ".pdf"), ("fat", ".docx"),
])
def test_only_files_exposed_directly_in_standard_folders_count(tmp_path, key, extension):
    paths = make_documents(tmp_path, "50606318", missing={key})
    nested = paths[key] / "archive"
    nested.mkdir()
    (nested / f"hidden{extension}").write_bytes(b"nested file must not count")

    result = DocumentVerifier(tmp_path).verify(order("50606318"), "Manual")

    assert getattr(result, key) == "Missing"


def test_multiple_drawings_are_accepted(tmp_path):
    paths = make_documents(tmp_path, "100")
    (paths["drawings"] / "second.pdf").write_bytes(b"x")
    assert DocumentVerifier(tmp_path).verify(order(), "Manual").drawings == "Yes"


def test_unavailable_root_is_error(tmp_path):
    with pytest.raises(NetworkUnavailableError):
        DocumentVerifier(tmp_path / "missing").verify(order(), "Manual")

