import unittest
from pathlib import Path

from stoa.tools.mep_tools import ifc_piping

CLINIC = Path("junction-dataset-preview/data/ifc_models/bsc-clinic_clinic-plumbing.ifc")


@unittest.skipUnless(CLINIC.exists(), "dataset not extracted")
class PipingTests(unittest.TestCase):
    def test_overview_counts(self):
        a = ifc_piping(str(CLINIC))
        self.assertIn("pipes: 2893, fittings: 3318, valves: 254, fixtures: 119, pumps: 1", a.value)
        self.assertIn("pipe length 3246.9 m", a.value)

    def test_one_size_of_one_system(self):
        a = ifc_piping(str(CLINIC), "pipe", ["size"], system="sanitary", only="100 mm")
        self.assertAlmostEqual(a.value, 409.627, places=2)
        self.assertEqual(a.unit, "m")

    def test_fittings_follow_system_type_not_classification(self):
        # 999 hot-water fittings carry System Classification "Domestic Cold Water";
        # port connectivity shows they sit on hot-water pipes.
        a = ifc_piping(str(CLINIC), "fitting", ["system"], only="Domestic Hot Water")
        self.assertEqual(a.value, 1048)

    def test_storey_uses_revit_level(self):
        # Revit put 1359 fittings on the footing storey; their Level is Level 1 or 2.
        a = ifc_piping(str(CLINIC), "fitting", ["storey"])
        self.assertIn("Level 1: 2146 fittings", a.value)
        self.assertIn("Level 2: 1170 fittings", a.value)


if __name__ == "__main__":
    unittest.main()
