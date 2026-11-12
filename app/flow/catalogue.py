from __future__ import annotations

from dataclasses import dataclass
import re

from app.flow.models import FlowDclLine


@dataclass(frozen=True)
class MaterialRule:
    key: str
    category: str
    serial_template: str
    part_codes: tuple[str, ...] = ()
    description_pattern: str | None = None
    metre_only: bool = False
    always_per_unit: bool = False
    quantity_suffix_over_20: bool = False

    def matches(self, line: FlowDclLine) -> bool:
        code = (line.part_code or "").strip().casefold()
        if self.part_codes and code in {item.casefold() for item in self.part_codes}:
            return True
        return bool(self.description_pattern and re.search(self.description_pattern, line.description, re.IGNORECASE))


BUILTIN_CATALOGUE_VERSION = 2
BUILTIN_RULES = (
    MaterialRule("AE02997", "IC43 Coupler", "{SO}-CPL-IC43-{NN}", ("AE02997",)),
    MaterialRule("AE07259-02", "QPDM", "{SO}-QPDM-{NN}", ("AE07259-02",)),
    MaterialRule("AE07264", "Portable PDM", "{SO}-POR-{NN}", ("AE07264",)),
    MaterialRule("AE01676-03", "BC48 Coupler", "{SO}-CPL-BC48-{NN}", ("AE01676-03",)),
    MaterialRule("AE04249", "HVI", "{SO}-HVI-{NN}", ("AE04249",)),
    MaterialRule("AE07262", "Portable PDM", "{SO}-POR-{NN}", ("AE07262",)),
    MaterialRule("DRAIN_VALVE", "Drain Valve Coupler", "{SO}-TVC-{NN}", description_pattern=r"\bdrain\s+valve\s+coupler\b"),
    MaterialRule("AE02909", "Window Material", "{SO}-WIN-{NN}", ("AE02909",)),
    MaterialRule("AE02908", "IC44 Flange", "{SO}-CPL-IC44-{NN}", ("AE02908",)),
    MaterialRule("UPS", "UPS", "{SO}-CAB-UPS-{NN}", description_pattern=r"\bUPS\b.*(?:3\s*kVA|RT3U|hotswap)"),
    MaterialRule("BATTERY", "Battery", "{SO}-CAB-Battery-{NN}", description_pattern=r"battery\s+pack.*Eaton\s+9PX3000IRTBHP"),
    MaterialRule("AE02910", "UHF Coupler", "{SO}-CPL-{NN}", ("AE02910",)),
    MaterialRule("SERVER", "Server", "{SO}-SER-{NN}", description_pattern=r"^\s*server\b"),
    MaterialRule("AE04314-85", "WC15 Coupler", "{SO}-CPL-WC15-{NN}", ("AE04314-85",)),
    MaterialRule("AE02092", "PDM Controller", "{SO}-OCU-PCB92-{NN}", ("AE02092",)),
    MaterialRule("AE02091", "Data Acquisition", "{SO}-OCU-PCB91-{NN}", ("AE02091",)),
    MaterialRule("CEK00378", "Ethernet Switch", "{SO}-OCU-Switch-{NN}", ("CEK00378",)),
    MaterialRule("CFL00247", "7-core cable", "{SO}-CFL00247-{Q}m", ("CFL00247",), metre_only=True),
    MaterialRule("CFL00335", "UHF cable", "{SO}-CFL00335-{Q}m", ("CFL00335",), metre_only=True),
    MaterialRule("OCU", "OCU", "{SO}-OCU-{NN}", description_pattern=r"\b(?:optical\s+converter\s+unit|OCU)\b", always_per_unit=True),
)


class MaterialCatalogue:
    def __init__(self, rules=BUILTIN_RULES, shared_prefixes: dict[str, str] | None = None):
        shared_rules = tuple(
            MaterialRule(
                key=f"SHARED:{part}", category=prefix,
                serial_template=f"{{SO}}-{prefix}-{{NN}}",
                part_codes=(part,), quantity_suffix_over_20=True,
            )
            for part, prefix in (shared_prefixes or {}).items()
        )
        # Existing approved nomenclature always wins over Sheet 2 mappings.
        self.rules = tuple(rules) + shared_rules

    def builtin_match(self, part_number: str) -> MaterialRule | None:
        code = str(part_number or "").strip().casefold()
        return next((
            rule for rule in BUILTIN_RULES
            if rule.part_codes and code in {item.casefold() for item in rule.part_codes}
        ), None)

    def match(self, line: FlowDclLine) -> MaterialRule | None:
        builtins = [rule for rule in self.rules if not rule.key.startswith("SHARED:")]
        builtin_exact = [rule for rule in builtins if rule.part_codes and rule.matches(line)]
        if builtin_exact:
            return builtin_exact[0]
        descriptions = [rule for rule in builtins if not rule.part_codes and rule.matches(line)]
        if len(descriptions) == 1:
            return descriptions[0]
        shared_exact = [
            rule for rule in self.rules
            if rule.key.startswith("SHARED:") and rule.matches(line)
        ]
        return shared_exact[0] if shared_exact else None
