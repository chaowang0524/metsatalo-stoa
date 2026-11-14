import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from stoa import pipeline
from stoa.answer import Answer
from stoa.tools import vision_tools
from stoa.ui import uploads


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, "JPEG")
    return buf.getvalue()


class UploadTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.patches = [
            patch.object(uploads, "UPLOADS", tmp / "up"),
            patch.object(uploads, "DOCS", tmp / "docs"),
            patch.object(uploads, "INDEX", tmp / "docs" / "index.json"),
            patch.object(uploads._pool, "submit"),  # no facts jobs, no Gemini calls
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def test_kind_is_read_from_the_files_not_a_path(self):
        res = uploads.save([
            ("plan.dxf", b"0"), ("plan.pdf", b"%PDF"),  # one CAD drawing, shown as its PDF
            ("house.ifc", b"ISO"), ("house_plan-01.svg", b"<svg/>"),  # the IFC belongs to the plan
            ("Scan 1.JPG", _jpeg()),  # raster: converted to PNG, a floor plan
            ("notes.txt", b"x"),
        ])
        self.assertEqual(res["skipped"], ["notes.txt"])
        docs = {json.loads(p.read_text(encoding="utf-8"))["file"]: json.loads(p.read_text(encoding="utf-8"))
                for p in uploads.DOCS.glob("*.json") if p.name != "index.json"}
        self.assertEqual(sorted(docs), ["Scan 1.JPG", "house_plan-01.svg", "plan.pdf"])
        self.assertEqual(docs["plan.pdf"]["source"], "swiss_dwellings")
        self.assertEqual(docs["house_plan-01.svg"]["source"], "ifc_models")
        scan = docs["Scan 1.JPG"]
        self.assertEqual((scan["source"], scan["kind"], scan["status"]), ("cubicasa5k", "raster", "processing"))
        self.assertTrue(scan["path"].endswith(".png") and Path(scan["path"]).exists())
        self.assertEqual((scan["view"]["width"], scan["view"]["height"]), (40, 30))
        self.assertEqual(len(uploads.index()), 3)

    def test_uploaded_floor_plan_reaches_the_floor_plan_vision_path(self):
        with patch.object(vision_tools, "ask_vision_model", return_value=Answer(3)) as vision:
            pipeline.answer(Path("out/uploads/x_plan.png"), "How many windows?", router="hybrid", floor_plan=True)
        self.assertEqual(vision.call_args.kwargs["endpoint"], "gemini")
        self.assertTrue(vision.call_args.kwargs["floor_plan"])


if __name__ == "__main__":
    unittest.main()
