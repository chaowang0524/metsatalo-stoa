"""Result type shared by every tool: a value plus the evidence a human can check."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Evidence:
    """One thing the answer was computed from.

    `ref` identifies it in the source file (an IFC GlobalId, a DXF entity handle,
    a PDF text span). `bbox` is optional drawing coordinates for highlighting.
    """

    ref: str
    label: str = ""
    value: Any = None
    bbox: tuple[float, float, float, float] | None = None
    page: int | None = None


@dataclass
class Answer:
    value: Any
    unit: str | None = None
    method: str = ""  # how it was computed, e.g. "IFC BaseQuantities.NetVolume"
    deterministic: bool = True
    evidence: list[Evidence] = field(default_factory=list)
    note: str = ""

    def summary(self, max_evidence: int = 10) -> dict:
        """Compact form, safe to feed back to an LLM or print."""
        return {
            "value": self.value,
            "unit": self.unit,
            "method": self.method,
            "deterministic": self.deterministic,
            "evidence_count": len(self.evidence),
            "evidence": [
                {"ref": e.ref, "label": e.label, "value": e.value}
                for e in self.evidence[:max_evidence]
            ],
            "note": self.note,
        }


class ToolError(Exception):
    """The tool could not answer, e.g. the named room or storey doesn't exist.

    The message is shown to the LLM so it can retry with other arguments.
    """
