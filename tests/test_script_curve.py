import json
import math
import tempfile
import unittest
from pathlib import Path
from organizer.local_tools.script_curve import load_script, interpolate, script_speed, curve_points, percentile


class CurveTests(unittest.TestCase):
    def script(self, actions):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name)/'test.funscript'
        path.write_text(json.dumps({'actions': actions}))
        return load_script(path)

    def test_interpolation_boundaries_and_offset(self):
        script = self.script([dict(at=0, pos=0), dict(at=1000, pos=100)])
        self.assertEqual(interpolate(.5, script['times'], script['positions']), 50)
        self.assertEqual(curve_points(script, .25, .75, 0), [(.25, 25), (.75, 75)])
        self.assertEqual(curve_points(script, 2, 3, 0), [])

    def test_heatmap_speed_and_unscripted_tail(self):
        script = self.script([dict(at=0, pos=0), dict(at=1000, pos=100), dict(at=30000, pos=0)])
        result = script_speed(script, [.5, 15, 31], 1)
        self.assertEqual(result[0], 100)
        self.assertTrue(math.isnan(result[1]) and math.isnan(result[2]))

    def test_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            self.script([dict(at=0, pos=0), dict(at=1, pos=101)])

    def test_percentile_matches_linear_rank(self):
        self.assertAlmostEqual(percentile([0, 100], .95), 95)
