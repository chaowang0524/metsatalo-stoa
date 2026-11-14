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
