import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from stoa import cubicasa_gemini as detection


def response(text=None, finish="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(
            content=text or '{"icons":[],"rooms":[]}'))],
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100, total_tokens=1100),
    )


class CubicasaGeminiTests(unittest.TestCase):
    def test_normalized_yx_becomes_original_pixel_xy(self):
        text = json.dumps({"icons": [{"class": "Window", "box_2d": [100, 200, 300, 400]}],
                           "rooms": [{"class": "Bath", "box_2d": [0, 0, 1000, 1000]}]})
        data = detection.to_cache(text, "a.png", (2000, 500))
        self.assertEqual(data["icons"][0]["bbox"], [400.0, 50.0, 800.0, 150.0])
        self.assertEqual(data["rooms"][0]["bbox"], [0.0, 0.0, 2000.0, 500.0])
        self.assertEqual(set(data), {"file", "icons", "rooms"})

    def test_invalid_boxes_and_classes_are_rejected(self):
        for cls, box in [("Door", [0, 0, 0, 10]), ("Door", [20, 0, 10, 20]),
                         ("Door", [-1, 0, 10, 20]), ("Door", [0, 0, 1001, 20]),
                         ("Door", [True, 0, 10, 20]), ("Door", [0, 0, float("nan"), 20]),
                         ("Shower", [0, 0, 10, 20])]:
            with self.subTest(cls=cls, box=box), self.assertRaises(ValueError):
                detection.to_cache(json.dumps({"icons": [{"class": cls, "box_2d": box}],
                                               "rooms": []}), "a.png", (100, 100))

    def test_duplicates_and_incomplete_json_are_rejected(self):
        item = {"class": "Door", "box_2d": [0, 0, 10, 20]}
        for text in ('{"icons":[]}', '{"icons":[],"rooms":[',
                     json.dumps({"icons": [item, item], "rooms": []})):
            with self.subTest(text=text), self.assertRaises(ValueError):
                detection.to_cache(text, "a.png", (100, 100))

    def test_atomic_write_hash_matches_file_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out.json"
            digest = detection._atomic_json(path, {"unicode": "ä"})
            self.assertEqual(digest, detection._sha(path.read_bytes()))

    def test_resume_checks_input_settings_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path, cache = Path(directory) / "a.png", Path(directory) / "cache"
            Image.new("RGB", (100, 50), "white").save(path)
            with patch.object(detection.verda, "chat", return_value=response()) as chat:
                self.assertEqual(detection.analyse(path, cache)["status"], "complete")
                self.assertEqual(detection.analyse(path, cache)["status"], "cached")
                self.assertEqual(chat.call_count, 1)
                self.assertEqual(detection.analyse(path, cache, effort="medium")["status"], "complete")
                Image.new("RGB", (100, 50), "black").save(path)
                self.assertEqual(detection.analyse(path, cache, effort="medium")["status"], "complete")
                (cache / "a.json").write_text('{"file":"wrong.png","icons":[],"rooms":[]}')
                self.assertEqual(detection.analyse(path, cache, effort="medium")["status"], "complete")
                self.assertEqual(chat.call_count, 4)

    def test_truncated_attempt_is_billed_and_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            path, cache = Path(directory) / "a.png", Path(directory) / "cache"
            Image.new("RGB", (100, 50)).save(path)
            with patch.object(detection.verda, "chat", side_effect=[response(finish="length"), response()]) as chat, \
                 patch.object(detection.time, "sleep"):
                result = detection.analyse(path, cache)
            audit = json.loads((cache / "_audit/a.json").read_text())
            self.assertEqual(result["status"], "complete")
            self.assertAlmostEqual(result["cost_usd"], 0.0045)
            self.assertEqual(len(audit["attempts"]), 2)
            self.assertIn("Incomplete", audit["attempts"][0]["error"])
            self.assertEqual(chat.call_args_list[0].kwargs["max_tokens"], 24576)
            self.assertEqual(chat.call_args_list[1].kwargs["max_tokens"], 49152)

    def test_failed_run_keeps_valid_previous_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path, cache = Path(directory) / "a.png", Path(directory) / "cache"
            Image.new("RGB", (100, 50)).save(path)
            with patch.object(detection.verda, "chat", return_value=response()):
                detection.analyse(path, cache)
            before = (cache / "a.json").read_bytes()
            with patch.object(detection.verda, "chat", return_value=response(text="invalid")):
                result = detection.analyse(path, cache, attempts=1, overwrite=True)
            self.assertEqual(result["status"], "failed")
            self.assertEqual((cache / "a.json").read_bytes(), before)
            self.assertGreater(result["cost_usd"], 0)

    def test_resume_after_truncation_starts_with_larger_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path, cache = Path(directory) / "a.png", Path(directory) / "cache"
            Image.new("RGB", (100, 50)).save(path)
            with patch.object(detection.verda, "chat", return_value=response(finish="length")):
                self.assertEqual(detection.analyse(path, cache, attempts=1)["status"], "failed")
            with patch.object(detection.verda, "chat", return_value=response()) as chat:
                self.assertEqual(detection.analyse(path, cache)["status"], "complete")
            self.assertEqual(chat.call_args.kwargs["max_tokens"], 49152)

    def test_output_at_maximum_budget_can_retry_with_medium_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            path, cache = Path(directory) / "a.png", Path(directory) / "cache"
            Image.new("RGB", (100, 50)).save(path)
            with patch.object(detection.verda, "chat", side_effect=[response(finish="length"), response()]) as chat, \
                 patch.object(detection.time, "sleep"):
                result = detection.analyse(path, cache, max_tokens=65536)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(chat.call_args_list[0].kwargs["reasoning_effort"], "high")
            self.assertEqual(chat.call_args_list[1].kwargs["reasoning_effort"], "medium")


if __name__ == "__main__":
    unittest.main()
