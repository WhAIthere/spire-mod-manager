import unittest
from window_layout import initial_geometry


class WindowLayoutTests(unittest.TestCase):
    def test_client_and_title_bar_fit_small_and_scaled_work_areas(self):
        for area in [(0, 0, 1920, 1040), (0, 0, 1366, 728), (0, 0, 960, 500), (0, 32, 1280, 680)]:
            with self.subTest(area=area):
                width, height, x, y = initial_geometry(area)
                self.assertGreaterEqual(x, area[0])
                self.assertGreaterEqual(y, area[1])
                self.assertLessEqual(x + width + 16, area[2])
                self.assertLessEqual(y + height + 44, area[3])
                self.assertLessEqual(height, 680)


if __name__ == "__main__":
    unittest.main()
