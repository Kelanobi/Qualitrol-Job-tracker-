from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import re

from app.flow.catalogue import MaterialCatalogue, MaterialRule
from app.flow.models import FlowDclLine, FlowReviewItem, ReviewStatus


METRE_UNITS = {"m", "metre", "metres", "meter", "meters"}


def format_quantity(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


class FlowSerialPolicy:
    def __init__(self, catalogue: MaterialCatalogue | None = None):
        self.catalogue = catalogue or MaterialCatalogue()

    def propose(self, lines: list[FlowDclLine]) -> list[FlowReviewItem]:
        counters: dict[str, int] = defaultdict(int)
        occupied: set[str] = set()
        results: list[FlowReviewItem] = []
        for line in lines:
            rule = self.catalogue.match(line)
            item = self._propose_line(line, rule, counters)
            duplicates = [serial for serial in item.proposed_serials if serial.casefold() in occupied]
            if duplicates:
                item.status = ReviewStatus.BLOCKED
                item.issues.append(f"Generated serial collision: {duplicates}.")
            occupied.update(serial.casefold() for serial in item.proposed_serials)
            results.append(item)
        return results

    def _propose_line(self, line: FlowDclLine, rule: MaterialRule | None, counters) -> FlowReviewItem:
        if line.quantity is None or line.quantity <= 0:
            return FlowReviewItem(line, None, None, [], None, ReviewStatus.BLOCKED, ["Quantity is missing or not positive."])
        fallback_part = None
        if rule is None:
            # User-approved fallback: when no named serial policy exists, use
            # the DCL part number rather than leaving the row without a serial.
            # Keep only serial-safe punctuation while preserving recognisable
            # part-number separators such as hyphens, dots and underscores.
            part = re.sub(r"[^A-Za-z0-9._-]+", "-", (line.part_code or "").strip()).strip("-").upper()
            if not part:
                return FlowReviewItem(
                    line, None, None, [], line.quantity, ReviewStatus.NEEDS_REVIEW,
                    ["No approved mapping or part number is available; enter a serial pattern."],
                )
            fallback_part = part
            rule = MaterialRule(
                key=f"PART:{part}",
                category=part,
                serial_template=(
                    f"{{SO}}-{part}-{{Q}}"
                    if line.quantity > 20
                    else f"{{SO}}-{part}-{{NN}}"
                ),
            )
        unit = (line.unit or "").strip().casefold()
        # The approved catalogue is authoritative for metre-only cable part codes.
        # DCL exports commonly show only the numeric Qty and omit the unit even
        # though CFL00247/CFL00335 are ordered and serialized by cable length.
        is_metre = rule.metre_only or unit in METRE_UNITS
        if rule.quantity_suffix_over_20 and line.quantity > 20:
            serials = [rule.serial_template.replace("{NN}", "{Q}").format(
                SO=line.so, Q=format_quantity(line.quantity), NN="01",
            )]
        elif fallback_part and line.quantity > 20:
            # Approved Flow fallback: one editable row representing the full
            # quantity, e.g. 50602018-CEH01026-105. OCU is matched by its
            # dedicated always-per-unit rule before this fallback is reached.
            serials = [rule.serial_template.format(
                SO=line.so, Q=format_quantity(line.quantity), NN="01",
            )]
        elif is_metre:
            serials = [rule.serial_template.format(SO=line.so, Q=format_quantity(line.quantity), NN="01")]
        else:
            if line.quantity != line.quantity.to_integral_value():
                return FlowReviewItem(line, rule.category, rule.serial_template, [], line.quantity, ReviewStatus.BLOCKED,
                                      ["Fractional piece quantity requires review."], rule.key)
            quantity = int(line.quantity)
            row_count = quantity if rule.always_per_unit or quantity < 20 else 1
            serials = []
            for _ in range(row_count):
                counters[rule.serial_template] += 1
                number = counters[rule.serial_template]
                serials.append(rule.serial_template.format(SO=line.so, NN=f"{number:02d}", Q=format_quantity(line.quantity)))
        issues = []
        if line.explicit_serials:
            if len(line.explicit_serials) != len(serials):
                issues.append("Explicit DCL serial count conflicts with the Flow row-count rule.")
                status = ReviewStatus.NEEDS_REVIEW
            else:
                serials = list(line.explicit_serials)
                status = ReviewStatus.RECOGNISED
        else:
            status = ReviewStatus.RECOGNISED
        if fallback_part:
            # Unmapped part-number proposals are intentionally presented for
            # user editing instead of being auto-approved like catalogue rules.
            status = ReviewStatus.NEEDS_REVIEW
        return FlowReviewItem(
            line, rule.category, rule.serial_template, serials, line.quantity,
            status, issues, rule.key,
        )
