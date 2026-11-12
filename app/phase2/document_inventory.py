from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from app.phase2.models import DocumentRecord
from app.services.document_verifier import NetworkUnavailableError


PHASE2_CATEGORIES = ("Specs", "QA", "DCL", "Drawings")
SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xlsm"}


def detect_document_type(category: str, path: Path) -> str:
    name = path.name.casefold()
    if category == "Specs" and path.suffix.casefold() == ".pdf":
        if "gdm" in name:
            return "gdm_dca"
        if "pdm" in name:
            return "pdm_dca"
        if "dca" in name:
            return "dca"
        return "technical_specification"
    if category == "QA" and path.suffix.casefold() in {".xlsx", ".xlsm"}:
        return "costed_bom" if "bom" in name or "cost" in name else "qa_workbook"
    if category == "DCL" and path.suffix.casefold() == ".pdf":
        if re.search(r"(?:^|[\s_-])gts?(?:[\s_.-]|$)", name):
            return "goods_transaction_report"
        return "despatch_checklist"
    if category == "Drawings" and path.suffix.casefold() == ".pdf":
        return "technical_drawing"
    return "unsupported"


def detect_revision(filename: str) -> str | None:
    match = re.search(r"(?:rev(?:ision)?[\s._-]*)([A-Z0-9]+)", filename, re.IGNORECASE)
    return match.group(1).upper() if match else None


class DocumentInventoryService:
    def __init__(self, document_root: Path, hash_limit_bytes: int = 0):
        self.document_root = Path(document_root)
        self.hash_limit_bytes = hash_limit_bytes

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def inventory(self, so: str) -> list[DocumentRecord]:
        if not self.document_root.exists() or not self.document_root.is_dir():
            raise NetworkUnavailableError(f"Cannot access the document root at {self.document_root}.")
        order_root = self.document_root / so
        if not order_root.exists():
            return []
        records: list[DocumentRecord] = []
        for category in PHASE2_CATEGORIES:
            folder = order_root / category
            if not folder.exists():
                continue
            try:
                paths = self._candidate_paths(category, folder)
            except (PermissionError, OSError) as exc:
                raise NetworkUnavailableError(f"Cannot enumerate {folder}: {exc}") from exc
            for path in paths:
                try:
                    if not path.is_file() or path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
                        continue
                    stat = path.stat()
                    # Drawings folders can contain hundreds of large network files. Their
                    # content hashes are deferred until a drawing is selected for parsing;
                    # size/mtime still provide a fast initial manifest fingerprint.
                    checksum = (
                        self._sha256(path)
                        if self.hash_limit_bytes > 0
                        and category != "Drawings"
                        and stat.st_size <= self.hash_limit_bytes
                        else None
                    )
                    records.append(DocumentRecord(
                        category=category,
                        path=path,
                        filename=path.name,
                        extension=path.suffix.casefold(),
                        size=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime),
                        sha256=checksum,
                        detected_type=detect_document_type(category, path),
                        revision=detect_revision(path.name),
                        order_reference=so if so in path.name else None,
                        parse_status="pending" if detect_document_type(category, path) != "unsupported" else "unsupported",
                    ))
                except (PermissionError, OSError) as exc:
                    records.append(DocumentRecord(
                        category=category,
                        path=path,
                        filename=path.name,
                        extension=path.suffix.casefold(),
                        size=0,
                        modified_at=datetime.fromtimestamp(0),
                        sha256=None,
                        detected_type="unknown",
                        parse_status="error",
                        error=str(exc),
                    ))
        return records

    @staticmethod
    def _supported_files(paths) -> list[Path]:
        return sorted(
            (
                path for path in paths
                if path.is_file() and path.suffix.casefold() in SUPPORTED_EXTENSIONS
            ),
            key=lambda item: str(item).casefold(),
        )

    def _candidate_paths(self, category: str, folder: Path) -> list[Path]:
        """Return only current documents exposed directly in a category folder.

        Released Project jobs keep their active files directly inside the four
        standard category folders.  Nested folders commonly contain customer
        source packs, published copies and NFM archives; walking them on a network
        share can take minutes and causes archived or irrelevant files to be parsed.
        """
        direct = self._supported_files(folder.iterdir())

        if category == "Specs":
            selected = [
                path for path in direct
                if detect_document_type(category, path) in {"pdm_dca", "gdm_dca", "dca"}
            ]
        elif category == "QA":
            selected = [path for path in direct if detect_document_type(category, path) == "costed_bom"]
        elif category == "DCL":
            dcl_pdfs = [path for path in direct if path.suffix.casefold() == ".pdf"]
            selected = [
                path for path in direct
                if detect_document_type(category, path) == "despatch_checklist"
            ]
            # Keep directly exposed GT reports visible in the manifest, but the
            # Project parser intentionally skips them. Nested reports stay out.
            return dcl_pdfs
        else:  # Drawings
            selected = [path for path in direct if detect_document_type(category, path) == "technical_drawing"]

        return selected

