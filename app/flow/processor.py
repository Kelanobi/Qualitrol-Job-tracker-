from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from app.flow.dcl_parser import extract_flow_dcl_pdf
from app.flow.eligibility import FlowEligibilityService
from app.flow.models import FlowDclLine, FlowReviewItem, ReviewStatus
from app.flow.serial_policy import FlowSerialPolicy
from app.flow.catalogue import MaterialCatalogue
from app.flow.prefix_registry import FlowPrefixRegistry
from app.services.document_verifier import NetworkUnavailableError


@dataclass
class FlowRun:
    order: object
    dcl_files: list[Path]
    lines: list[FlowDclLine]
    review_items: list[FlowReviewItem]
    issues: list[str]
    run_id: int | None = None
    review_records: list[tuple[int, FlowReviewItem]] | None = None


class FlowProcessor:
    def __init__(self, workbook_path: Path, document_root: Path, worksheet_name="Sheet1"):
        self.eligibility = FlowEligibilityService(workbook_path, worksheet_name)
        self.document_root = Path(document_root)
        self.prefixes = FlowPrefixRegistry(workbook_path)

    def prepare(self, so: str, order=None) -> FlowRun:
        order = order or self.eligibility.find_flow(so)
        dcl_folder = self.document_root / order.so / "DCL"
        if not self.document_root.exists():
            raise NetworkUnavailableError(f"Cannot access the document root at {self.document_root}.")
        if not dcl_folder.exists():
            return FlowRun(order, [], [], [], [f"DCL folder is missing for Flow order {order.so}."])
        # Only DCLs exposed directly in the standard folder count. Nested
        # folders are archives/source packs and must never be searched.
        files = sorted(dcl_folder.glob("*.pdf"), key=lambda value: str(value).casefold())
        files = [path for path in files if not re.search(
            r"(?:^|[\s_-])gts?(?:[\s_.-]|$)", path.name.casefold()
        )]
        lines = []
        issues = []
        seen = set()
        for path in files:
            extraction = extract_flow_dcl_pdf(path, order.so)
            issues.extend(extraction.issues)
            for line in extraction.lines:
                key = (line.part_code, line.description.casefold(), line.quantity, (line.unit or "").casefold(), tuple(line.explicit_serials))
                if key in seen:
                    issues.append(f"Possible duplicate DCL line withheld from {path.name}: {line.raw_text}")
                    continue
                seen.add(key)
                lines.append(line)
        # Read Sheet 2 for every preview so mappings added on another laptop
        # apply immediately without restarting this standalone app.
        policy = FlowSerialPolicy(MaterialCatalogue(shared_prefixes=self.prefixes.read()))
        review = policy.propose(lines)
        if len({line.customer.casefold() for line in lines if line.customer}) > 1:
            issues.append("DCL files disagree on the commercial customer.")
            for item in review:
                item.status = ReviewStatus.BLOCKED
                item.issues.append("Commercial customer conflict.")
        return FlowRun(order, files, lines, review, issues)
