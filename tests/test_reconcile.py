import unittest

from stoa.answer import Evidence
from stoa.pipeline import _reconcile_count

ROOMS = [Evidence(ref=f"h{i}", label=f"0.0{i} Pièce") for i in range(8)]
DXF = [{"tool": "dxf_count_rooms"}, {"tool": "dxf_room_list"}]


class ReconcileCount(unittest.TestCase):
    def test_count_follows_citations(self):
        value, note = _reconcile_count(9, "count", ROOMS, [f"e{i}" for i in range(8)], DXF)
        self.assertEqual(value, 8)
        self.assertIn("Corrected from 9", note)

    def test_consistent_count_unchanged(self):
        self.assertEqual(_reconcile_count(8, "count", ROOMS, ["e1"], DXF), (8, ""))

    def test_duplicate_refs_counted_once(self):
        value, _ = _reconcile_count(9, "count", ROOMS + ROOMS[:1], ["e1"], DXF)
        self.assertEqual(value, 8)

    def test_schedule_rows_left_alone(self):
        rows = [{"tool": "pdf_table_rows"}]
        self.assertEqual(_reconcile_count(5, "count", ROOMS[:2], ["e1", "e2"], rows), (5, ""))

    def test_areas_and_uncited_left_alone(self):
        self.assertEqual(_reconcile_count(65.2, "m2", ROOMS, ["e1"], DXF), (65.2, ""))
        self.assertEqual(_reconcile_count(9, "count", ROOMS, None, DXF), (9, ""))


if __name__ == "__main__":
    unittest.main()
