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

    def test_pdf_preview_is_published_before_detection(self):
        for text in ("", "Vector floor plan with an extractable text layer"):
            with self.subTest(scanned=not text):
                with uploads.pymupdf.open() as pdf:
                    page = pdf.new_page(width=400, height=300)
                    if text:
                        page.insert_text((20, 30), text)
                    data = pdf.tobytes()
                result = uploads.save([("plan.pdf", data)])
                doc_path = uploads.DOCS / f"{result['drawings'][0].split('/', 1)[1]}.json"
                doc = json.loads(doc_path.read_text(encoding="utf-8"))
                self.assertEqual(doc["view"]["background"], "")

                def detect(png, *args):
                    preview = json.loads(doc_path.read_text(encoding="utf-8"))
                    self.assertEqual(preview["status"], "processing")
                    self.assertEqual(preview["facts"], [])
                    self.assertEqual(preview["view"]["background"], f"/uploads/{png.name}")
                    with Image.open(png) as image:
                        self.assertEqual((preview["view"]["width"], preview["view"]["height"]), image.size)
                    row = next(r for r in uploads.index() if r["id"] == doc["id"])
                    self.assertEqual(row["status"], "processing")
                    self.assertEqual(row["background"], preview["view"]["background"])
                    return {"view": preview["view"], "facts": []}

                with patch.object(uploads, "_detect", side_effect=detect) as detection:
                    uploads._process(doc)
                detection.assert_called_once()
                completed = json.loads(doc_path.read_text(encoding="utf-8"))
                self.assertEqual(completed["status"], "done")
                self.assertTrue(completed["view"]["background"].endswith(".png"))

    def test_failed_pdf_detection_keeps_the_preview(self):
        with uploads.pymupdf.open() as pdf:
            pdf.new_page(width=400, height=300)
            data = pdf.tobytes()
        result = uploads.save([("plan.pdf", data)])
        doc_path = uploads.DOCS / f"{result['drawings'][0].split('/', 1)[1]}.json"
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        with patch.object(uploads, "_detect", side_effect=RuntimeError("detection unavailable")):
            uploads._process(doc)
        failed = json.loads(doc_path.read_text(encoding="utf-8"))
        self.assertEqual(failed["status"], "failed")
        self.assertIn("detection unavailable", failed["error"])
        self.assertTrue(failed["view"]["background"].endswith(".png"))
        self.assertEqual(uploads.index()[0]["background"], failed["view"]["background"])


class PdfRoomTests(unittest.TestCase):
    def test_room_stamps_match_the_reference_counts(self):
        from collections import Counter
        from stoa.tools.pdf_tools import pdf_rooms
        rooms, units = pdf_rooms("junction-dataset-preview/data/swiss_dwellings/site1000_bldg2082_floor9385.pdf")
        names = Counter(r["label"] for r in rooms)
        self.assertEqual(len(units), 4)  # questions.jsonl: 4 apartments
        self.assertEqual((names["Zimmer"], names["Küche"], names["Reduit"], names["Wohnen/Essen"], names["Treppenhaus"]),
                         (12, 4, 2, 1, 1))
        self.assertEqual(next(r for r in rooms if r["number"] == "2.01")["area"], 15.61)


if __name__ == "__main__":
    unittest.main()
