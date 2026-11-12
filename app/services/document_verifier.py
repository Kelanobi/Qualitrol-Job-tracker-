from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from app.models import Order, Result, VerificationResult


class NetworkUnavailableError(OSError):
    pass


class DocumentVerifier:
    def __init__(self, document_root: Path):
        self.document_root = Path(document_root)

    def _root_available(self) -> None:
        try:
            if not self.document_root.exists() or not self.document_root.is_dir():
                raise NetworkUnavailableError(
                    f"Cannot access the document root at {self.document_root}. "
                    "Check your network connection and mapped drive availability."
                )
            next(self.document_root.iterdir(), None)
        except NetworkUnavailableError:
            raise
        except OSError as exc:
            raise NetworkUnavailableError(
                f"Cannot access the document root at {self.document_root}: {exc}"
            ) from exc

    @staticmethod
    def _check_folder(folder: Path, extension: str | tuple[str, ...]) -> tuple[str, str | None]:
        try:
            if not folder.exists():
                parent = folder.parent
                if not parent.exists() or not parent.is_dir():
                    return Result.MISSING.value, f"Required folder does not exist: {folder}"
                next(parent.iterdir(), None)
                return Result.MISSING.value, f"Required folder does not exist: {folder}"
            if not folder.is_dir():
                return Result.ERROR.value, f"Expected a folder but found another item: {folder}"
            extensions = (extension,) if isinstance(extension, str) else extension
            accepted = {item.casefold() for item in extensions}
            found = any(
                item.is_file() and item.suffix.casefold() in accepted
                for item in folder.iterdir()
            )
            return (Result.YES.value, None) if found else (
                Result.MISSING.value,
                f"No {' or '.join(item.upper() for item in extensions)} file was found in {folder}.",
            )
        except PermissionError as exc:
            return Result.ERROR.value, f"Access was denied when reading {folder}: {exc}"
        except OSError as exc:
            return Result.ERROR.value, f"Unable to read {folder}: {exc}"

    def verify(
        self,
        order: Order,
        search_mode: str,
        progress: Callable[[str], None] | None = None,
    ) -> VerificationResult:
        progress = progress or (lambda _message: None)
        self._root_available()
        result = VerificationResult(order=order, search_mode=search_mode, started_at=datetime.now())
        order_root = self.document_root / order.number
        checks = [
            ("specs", order_root / "Specs", ".pdf", "Checking Specs"),
            ("qa", order_root / "QA", ".xlsx", "Checking QA"),
            ("dcl", order_root / "DCL", ".pdf", "Checking DCL"),
        ]
        if order.category.casefold() == "project":
            checks.append(("drawings", order_root / "Drawings", ".pdf", "Checking Drawings"))
            # FAT is presence-only. Never open or inspect its Word document.
            checks.append(("fat", order_root / "FAT", (".docx", ".doc"), "Checking FAT"))
        else:
            result.drawings = Result.NOT_APPLICABLE.value
            result.fat = Result.NOT_APPLICABLE.value

        for field_name, folder, extension, message in checks:
            progress(message)
            value, detail = self._check_folder(folder, extension)
            setattr(result, field_name, value)
            result.paths[field_name] = folder
            if value == Result.ERROR.value and detail:
                result.errors.append(detail)
                result.error_category = "Access error"
        result.completed_at = datetime.now()
        return result

