import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stoa import pipeline, verda
from stoa import eval as evaluation
from stoa.answer import Answer, Evidence
from stoa.inspect_file import FileInfo
from stoa.tools import vision_tools as vision


class InventoryTests(unittest.TestCase):
    def test_count_only_for_cubicasa_in_english_or_finnish(self):
        for question in ("How many doors?", "Kuinka monta ikkunaa?"):
            self.assertTrue(vision._cubicasa_count("data/CUBICASA5K/a.png", question))
            self.assertFalse(vision._cubicasa_count("data/other/a.png", question))
        self.assertFalse(vision._cubicasa_count("data/cubicasa5k/a.png", "What is the floor area?"))

    def test_inventory_counts_objects_and_retains_scope_and_evidence(self):
        for objects in ([], ["kitchen: left wall", "bathroom: beside entrance"]):
            response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                message=SimpleNamespace(content=json.dumps({
                    "scope": "Apartment A; excludes adjacent fragment", "objects": objects,
                    "unit": "count", "answer": 99})))])
            with self.subTest(objects=objects), \
                 patch.object(vision, "_png_bytes", return_value=b"fixture"), \
                 patch.object(verda, "chat", return_value=response), \
                 patch.object(verda, "usage_of", return_value={"cost_usd": 0.01}):
                result = vision.ask_vision_model("data/cubicasa5k/a.png", "How many sinks?", endpoint="gemini")
            self.assertEqual(result.value, len(objects))
            self.assertEqual([e.label for e in result.evidence], objects)
            self.assertIn("Apartment A", result.note)
            self.assertFalse(result.deterministic)

    def test_malformed_inventory_never_becomes_a_number(self):
        for text in ('{"scope":"A12","objects":[', '{"answer":12}',
                     '{"scope":"A","unit":"count","objects":"3"}',
                     '{"scope":"A","unit":"count","objects":[3]}',
                     '{"scope":"A","unit":"count","objects":["left", "LEFT"]}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                vision._count_inventory(text)

    def test_truncated_response_is_failure_even_if_json_is_complete(self):
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="length",
            message=SimpleNamespace(content='{"scope":"A","unit":"count","objects":[]}'))])
        with patch.object(vision, "_png_bytes", return_value=b"fixture"), \
             patch.object(verda, "chat", return_value=response), \
             patch.object(verda, "usage_of", return_value={"cost_usd": 0.01}):
            with self.assertRaises(vision.VisionResponseError) as caught:
                vision.ask_vision_model("data/cubicasa5k/a.png", "How many sinks?", endpoint="gemini")
        self.assertEqual(caught.exception.usage["cost_usd"], 0.01)

    def test_pipeline_retains_billed_usage_for_invalid_response(self):
        path = Path("data/cubicasa5k/a.png")
        with patch.object(pipeline, "inspect_file", return_value=FileInfo(path, "raster", image=path)), \
             patch.object(vision, "ask_vision_model", side_effect=vision.VisionResponseError(
                 "truncated", {"cost_usd": 0.02, "prompt_tokens": 10, "completion_tokens": 100})):
            result = pipeline.answer(path, "How many sinks?")
        row = result.to_json()
        self.assertEqual(row["error"], "truncated")
        self.assertEqual(row["cost_usd"], 0.02)
        self.assertEqual(row["llm_calls"], 1)
        self.assertIsNone(result.answer)

    def test_eval_preserves_complete_inventory_even_above_summary_limit(self):
        answer = Answer(60, unit="count", method=vision.INVENTORY_METHOD,
                        deterministic=False, note="Scope: A",
                        evidence=[Evidence(str(i), f"object {i}", 1) for i in range(60)])
        result = pipeline.Result("How many windows?", "data/cubicasa5k/a.png", answer)
        question = {"id": 1, "path": "data/cubicasa5k/a.png", "source": "cubicasa5k",
                    "question": "How many windows?", "type": "count", "answer": 60}
        with patch.object(evaluation, "answer", return_value=result):
            row = evaluation.run_one(question)
        self.assertEqual(len(row["answer"]["evidence"]), 60)
        self.assertEqual(row["answer"]["note"], "Scope: A")
        self.assertTrue(row["correct"])

    def test_count_unit_label_is_not_used_as_the_count(self):
        self.assertEqual(vision._count_inventory(
            '{"scope":"Apartment A","objects":["ground floor"],"unit":"floor"}'),
            ("Apartment A", ["ground floor"]))


if __name__ == "__main__":
    unittest.main()


class WindowCountTests(unittest.TestCase):
    def _ask(self, reply, boxes):
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content=json.dumps(reply)))])
        with patch.object(vision, "detector_windows", return_value=boxes), \
             patch.object(vision, "_window_image", return_value=(b"png", (1000, 500))), \
             patch.object(verda, "chat", return_value=response), \
             patch.object(verda, "usage_of", return_value={"cost_usd": 0.01}):
            return vision.ask_vision_model("data/cubicasa5k/a.png", "Kuinka monta ikkunaa?", endpoint="gemini")

    def test_confirmed_boxes_plus_added_points(self):
        boxes = [[10, 10, 20, 20], [30, 30, 40, 40], [50, 50, 60, 60]]
        result = self._ask({"scope": "main flat",
                            "boxes": [{"id": 1, "accept": True}, {"id": 2, "accept": False},
                                      {"id": 3, "accept": True}, {"id": 3, "accept": True}],
                            "added": [{"where": "bedroom, north wall", "point": [500, 250]}]}, boxes)
        self.assertEqual(result.value, 3)
        # Confirmed boxes keep the UI's fact refs; added ones are placed from the point.
        self.assertEqual([e.ref for e in result.evidence], ["window-0", "window-2", "window+1"])
        self.assertEqual(result.evidence[2].bbox, (238.0, 238.0, 262.0, 262.0))
        self.assertFalse(result.deterministic)

    def test_box_that_does_not_exist_is_dropped(self):
        result = self._ask({"scope": "A", "boxes": [{"id": 1, "accept": True}, {"id": 9, "accept": True}],
                            "added": []}, [[0, 0, 5, 5]])
        self.assertEqual(result.value, 1)
        self.assertIn("[9]", result.note)

    def test_without_detector_the_model_places_every_window(self):
        result = self._ask({"scope": "A", "added": [{"where": "kitchen", "point": [100, 100]}]}, None)
        self.assertEqual(result.value, 1)
        self.assertEqual(result.evidence[0].ref, "window+1")

    def test_malformed_point_fails(self):
        with self.assertRaises(vision.VisionResponseError):
            self._ask({"scope": "A", "boxes": [], "added": [{"where": "x", "point": [1200, 5]}]}, [])
