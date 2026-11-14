import unittest
from pathlib import Path

from stoa.tools.dxf_tools import dxf_baseboard_length

PLAN = Path("junction-dataset-preview/data/swiss_dwellings/site4237_bldg7396_floor29509.dxf")


@unittest.skipUnless(PLAN.exists(), "dataset not extracted")
class BaseboardTests(unittest.TestCase):
    def test_room_with_three_doors(self):
        # 2.05 has two single doors and a double door: 3.88 m of its 16.78 m outline.
        a = dxf_baseboard_length(str(PLAN), room="2.05")
        self.assertAlmostEqual(a.value, 12.90, places=2)
        self.assertEqual(len(a.evidence), 3)  # three runs between the openings

    def test_runs_under_windows(self):
        # 2.02 has one 0.79 m door and a window; the window side keeps its baseboard.
        a = dxf_baseboard_length(str(PLAN), room="2.02")
        self.assertAlmostEqual(a.value, 14.85, places=2)

    def test_exclude_wet_rooms(self):
        every = dxf_baseboard_length(str(PLAN)).value
        dry = dxf_baseboard_length(str(PLAN), exclude_wet=True)
        self.assertLess(dry.value, every)
        self.assertNotIn("Bad", dry.note)


if __name__ == "__main__":
    unittest.main()
