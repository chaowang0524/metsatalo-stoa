"""Step 1 of the pipeline: look at the file and decide which tools apply. No LLM."""

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

RASTER = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


@dataclass
class FileInfo:
    path: Path
    kind: str  # "ifc" | "dxf" | "vector_pdf" | "raster" | "unknown"
    ifc: Path | None = None  # IFC model behind this drawing, if any
    dxf: Path | None = None  # CAD vectors for this drawing, if any
    pdf_text: bool = False  # PDF has an extractable text layer
    image: Path | None = None  # something renderable for the vision fallback
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        bits = [f"file: {self.path.name} ({self.kind})"]
        if self.ifc:
            bits.append(f"BIM model available: {self.ifc.name}")
        if self.dxf:
            bits.append(f"CAD vectors available: {self.dxf.name}")
        if self.pdf_text:
            bits.append("PDF has a text layer")
        bits += self.notes
        return "; ".join(bits)


def _source_ifc(path: Path) -> Path | None:
    """Plans rendered from an IFC are named <model>_<plan-level>.pdf/.svg."""
    stem = path.stem
    candidates = sorted(path.parent.glob("*.ifc"), key=lambda p: -len(p.stem))
    for c in candidates:
        if stem.startswith(c.stem + "_"):
            return c
    return None


def inspect_file(path: str | Path) -> FileInfo:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".ifc":
        return FileInfo(path, "ifc", ifc=path)
    if ext == ".dxf":
        return FileInfo(path, "dxf", dxf=path)
    if ext in RASTER:
        return FileInfo(path, "raster", image=path)
    if ext in (".pdf", ".svg"):
        info = FileInfo(path, "vector_pdf", image=path)
        info.ifc = _source_ifc(path)
        sibling = path.with_suffix(".dxf")
        if sibling.exists():
            info.dxf = sibling
        if ext == ".pdf":
            with pymupdf.open(path) as doc:
                text = "".join(p.get_text() for p in doc)
                n_drawings = sum(len(p.get_drawings()) for p in doc)
            info.pdf_text = len(text.strip()) > 20
            if not info.pdf_text and n_drawings < 10:
                info.kind = "raster"  # scanned sheet: pixels only
                info.notes.append("scanned PDF without vectors")
        else:
            info.pdf_text = True
        return info
    return FileInfo(path, "unknown")
