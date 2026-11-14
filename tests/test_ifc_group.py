import unittest
from pathlib import Path

from stoa.tools.ifc_tools import ifc_group_quantity

AC20 = Path("junction-dataset-preview/data/ifc_bench/ac20_arc.ifc")


@unittest.skipUnless(AC20.exists(), "dataset not extracted")
class IfcGroupQuantityTests(unittest.TestCase):
    """Expected numbers are the reference answers in questions.jsonl."""

    def test_floor_slab_volume_by_name(self):
        a = ifc_group_quantity(str(AC20), "IfcSlab", ["name"], "net_volume", predefined_type="FLOOR")
        self.assertIn("Decke-002: 3", a.value)
        self.assertIn("total net_volume 769.98 m3", a.value)
        self.assertEqual(len(a.evidence), 5)

    def test_wall_thickness_groups(self):
        a = ifc_group_quantity(str(AC20), "IfcWall", ["thickness"])
        self.assertIn("300 mm: 67", a.value)
        self.assertIn("200 mm: 54", a.value)

    def test_exterior_walls_from_layer_name(self):
        # ac20 has no IsExternal; the ArchiCAD layer "Außenwände" marks exterior walls.
        a = ifc_group_quantity(str(AC20), "IfcWall", ["type"], is_external=True)
        self.assertIn("Kalksandstein 2816491304 300: 46", a.value)
        self.assertIn("Kalksandstein 2774059904 300: 8", a.value)

    def test_only_returns_number(self):
        a = ifc_group_quantity(str(AC20), "IfcSlab", ["thickness"], "net_volume",
                               predefined_type="FLOOR", only="300 mm")
        self.assertAlmostEqual(a.value, 769.98, places=2)
        self.assertEqual(a.unit, "m3")


if __name__ == "__main__":
    unittest.main()
