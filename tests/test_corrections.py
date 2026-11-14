import unittest

from stoa.ui import corrections

DOC = {"facts": [
    {"group": "Components", "kind": "window", "label": "Windows",
     "items": [{"ref": f"window-{i}", "shape": {"bbox": [i, 0, i + 1, 1]}} for i in range(4)]},
    {"group": "Components", "kind": "door", "label": "Doors",
     "items": [{"ref": "door-0", "shape": {"bbox": [0, 0, 1, 1]}}]},
    {"group": "Rooms", "kind": "room", "label": "Rooms", "items": [{"ref": "room-0", "shape": {"bbox": [0, 0, 9, 9]}}]},
]}


def response(value, refs, unit="count"):
    return {"answer": {"value": value, "unit": unit},
            "items": [{"ref": r, "shape": {"bbox": [0, 0, 1, 1]}} for r in refs]}


class Corrections(unittest.TestCase):
    def test_count_drops_rejected_and_adds_added(self):
        refs = ["window-0", "window-1", "window-2", "window+1"]  # the last one placed by the vision model
        out = response(4, refs)
        edits = {"rejected": ["window:window-1", "door:door-0"],
                 "added": [{"id": "add-1", "kind": "window", "cx": 5, "cy": 5, "w": 2, "h": 1, "rot": 0},
                           {"id": "add-2", "kind": "door", "bbox": [0, 0, 1, 1]}]}
        corrections.apply(DOC, edits, "How many windows?", refs, out)
        self.assertEqual(out["answer"]["value"], 4)  # 4 - 1 + 1
        self.assertEqual(out["corrections"], {"applied": True, "kind": "window", "before": 4, "removed": 1, "added": 1})
        self.assertTrue(next(i for i in out["items"] if i["ref"] == "window-1")["rejected"])
        self.assertEqual(out["items"][-1]["shape"]["poly"], [[4, 4.5], [6, 4.5], [6, 5.5], [4, 5.5]])

    def test_zero_answer_uses_the_kind_the_question_names(self):
        out = response(0, [])
        corrections.apply(DOC, {"added": [{"id": "a", "kind": "door", "bbox": [0, 0, 1, 1]}], "rejected": []},
                          "how many doors are there", [], out)
        self.assertEqual(out["answer"]["value"], 1)

    def test_count_from_outside_the_plan_objects_is_flagged(self):
        out = response(28, ["p1:text"])
        corrections.apply(DOC, {"added": [], "rejected": ["window:window-1", "window:window-2"]},
                          "How many windows are there?", ["p1:text"], out)
        self.assertEqual(out["answer"]["value"], 28)
        self.assertEqual(out["corrections"], {"applied": False, "edits": 2, "rejected_in_evidence": 0, "kind": "window"})

    def test_non_count_answer_is_only_flagged(self):
        out = response(42.5, ["room-0"], unit="m2")
        corrections.apply(DOC, {"added": [], "rejected": ["room:room-0"]}, "Area of the flat?", ["room-0"], out)
        self.assertEqual(out["answer"]["value"], 42.5)
        self.assertEqual(out["corrections"], {"applied": False, "edits": 1, "rejected_in_evidence": 1, "kind": None})

    def test_mixed_kinds_are_not_corrected(self):
        refs = ["window-0", "door-0"]
        out = response(2, refs)
        corrections.apply(DOC, {"added": [], "rejected": ["window:window-0"]}, "openings?", refs, out)
        self.assertEqual(out["answer"]["value"], 2)
        self.assertFalse(out["corrections"]["applied"])

    def test_edits_unrelated_to_the_answer_change_nothing(self):
        refs = ["window-0"]
        out = response(1, refs)
        corrections.apply(DOC, {"added": [], "rejected": ["window:window-3"]}, "windows?", refs, out)
        self.assertEqual(out["answer"]["value"], 1)
        self.assertNotIn("corrections", out)


if __name__ == "__main__":
    unittest.main()
