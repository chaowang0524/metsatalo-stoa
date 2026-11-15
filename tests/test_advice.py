import unittest

from stoa.ui import advice


def room(ref, label, bbox):
    return {"ref": ref, "label": label, "shape": {"bbox": bbox}}


def obj(ref, bbox):
    return {"ref": ref, "label": ref, "shape": {"bbox": bbox}}


def comp(kind, items, exact=True):
    return {"group": "Components", "kind": kind, "label": kind, "deterministic": exact, "items": items}


# Hall (0-100) | Bedroom (102-200) | Bath (202-260) | Balcony (262-300), walls 2 thick, doors 40 wide.
DOC = {"facts": [
    comp("door", [obj("d-hall-bed", [100, 30, 102, 70]),  # in the wall between hall and bedroom
                  obj("d-entry", [-2, 30, 0, 70])]),  # in the hall's outer wall
    comp("window", [obj("w-bed", [120, -3, 160, 0])]),  # bedroom's outer wall, set into a thick wall
    comp("toilet", [obj("t-bath", [210, 10, 220, 20])]),
    comp("sink", []),
    {"group": "Spaces", "kind": "room", "label": "Rooms", "deterministic": True,
     "items": [room("r-hall", "1.01 Hall", [0, 0, 100, 100]), room("r-bed", "1.02 Bedroom", [102, 0, 200, 100]),
               room("r-bath", "Bath", [202, 0, 260, 100]), room("r-bal", "Balcony", [262, 0, 300, 100]),
               room("r-x", "Rum nr. 7", [0, 102, 100, 200])]},
]}


def pick(rule, types=None):
    return lambda question, unknown, context=None: (rule, types or {}, {"prompt_tokens": 50, "cost_usd": 0.0001})


class PlanChecks(unittest.TestCase):
    def test_room_types(self):
        self.assertEqual(advice.room_types("ELUTUBA+KÖÖK 57-1"), {"living", "kitchen"})
        self.assertEqual(advice.room_types("1.02 Stanza"), {"room"})
        self.assertEqual(advice.room_types("Cage d'escalier"), {"stair"})
        self.assertEqual(advice.room_types("Rum nr. 7"), set())

    def test_fallback_words(self):
        for q, rule in [("Do we need more doors for this floor plan", "doors"), ("Onko ovia tarpeeksi?", "doors"),
                        ("Are there enough windows?", "windows"), ("Mancano docce?", "bath_fixtures"),
                        ("How many doors are there?", None), ("What is the overall area?", None)]:
            self.assertEqual(advice.pick_rule_by_words(q), rule, q)

    def test_room_without_door_is_flagged(self):
        out = advice.answer(DOC, "Do we need more doors?", None, pick=pick("doors"))
        self.assertTrue(out["answer"]["value"].startswith("Yes, probably 1 more: Bath"))
        self.assertEqual(out["llm_calls"], 1)
        self.assertEqual(out["items"][0]["ref"], "r-bath")  # the balcony needs no door
        labels = {i["ref"]: i["label"] for i in out["items"]}
        self.assertEqual(labels["d-hall-bed"], "door: 1.01 Hall ↔ 1.02 Bedroom")
        self.assertIn("out of the plan", labels["d-entry"])
        self.assertIn("Room type unknown, not checked: Rum nr. 7", out["answer"]["note"])

    def test_router_types_unknown_rooms(self):
        out = advice.answer(DOC, "Do we need more doors?", None, pick=pick("doors", {"Rum nr. 7": {"bed"}}))
        self.assertIn("Rum nr. 7", out["answer"]["value"])

    def test_engineer_edits_are_included(self):
        edits = {"added": [{"id": "add-1", "kind": "door", "bbox": [200, 30, 202, 70]}], "rejected": ["door:d-entry"]}
        out = advice.answer(DOC, "Do we need more doors?", edits, pick=pick("doors"))
        self.assertNotIn("Bath", out["answer"]["value"].split(".")[0])
        self.assertIn("none leading out", out["answer"]["value"])  # the entrance door was rejected
        self.assertEqual(out["corrections"], {"applied": True, "kind": "door", "before": None, "removed": 1, "added": 1})
        self.assertNotIn("d-entry", [i["ref"] for i in out["items"]])

    def test_window_in_a_thick_wall_counts(self):
        out = advice.answer(DOC, "Enough windows?", None, pick=pick("windows"))
        self.assertTrue(out["answer"]["value"].startswith("Probably not"), out["answer"]["value"])

    def test_fixture_rules_are_hedged_even_on_cad(self):
        out = advice.answer(DOC, "Do the bathrooms have washbasins?", None, pick=pick("washbasins"))
        self.assertTrue(out["answer"]["value"].startswith("Possibly: no washbasin was found in the drawing's CAD/BIM data for Bath"))

    def test_toilet_inside_room(self):
        out = advice.answer(DOC, "Do we need more toilets?", None, pick=pick("toilets"))
        self.assertTrue(out["answer"]["value"].startswith("Probably not: the plan has 1 toilet"))
        self.assertIn("toilet: Bath", [i["label"] for i in out["items"]])

    def test_detected_plans_are_hedged(self):
        doc = {"facts": [dict(f, deterministic=False) for f in DOC["facts"]]}
        out = advice.answer(doc, "Do we need more doors?", None, pick=pick("doors"))
        self.assertTrue(out["answer"]["value"].startswith("Possibly: no door was found on the plan for Bath"))
        self.assertFalse(out["answer"]["deterministic"])

    def test_other_questions_fall_through(self):
        self.assertIsNone(advice.answer(DOC, "How many doors are there?", None, pick=pick(None)))
        self.assertIsNone(advice.answer({"facts": []}, "Do we need more doors?", None, pick=pick("doors")))
        doc = {"facts": [f for f in DOC["facts"] if f["kind"] != "closet"]}
        self.assertIsNone(advice.answer(doc, "Closets?", None, pick=pick("closets")))  # no closets in the data

    def test_router_failure_falls_back_to_words(self):
        def down(*a, **k):
            raise RuntimeError("endpoint down")
        out = advice.answer(DOC, "Do we need more doors?", None, pick=down)
        self.assertEqual(out["calls"], [{"tool": "plan_check", "args": {"rule": "doors"}}])
        self.assertEqual(out["llm_calls"], 0)


if __name__ == "__main__":
    unittest.main()
