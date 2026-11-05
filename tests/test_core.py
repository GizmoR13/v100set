import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from v100set.core import Settings, parse_gpus, load_targets

A = "GPU-11111111-2222-3333-4444-555555555555"
B = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SAMPLE = f"{A}, Tesla V100, 0000:0C:00.0, 23.45, 200, 100, 250, 36\n{B}, RTX A400, 0000:05:00.0, [N/A], 50, 10, 50, 34\n"


class HardwareTests(unittest.TestCase):
    def test_missing_draw_does_not_disable_power_control(self):
        cards = parse_gpus(SAMPLE)
        self.assertEqual(len(cards), 2)
        self.assertIsNone(cards[1].draw)
        self.assertTrue(cards[1].adjustable)

    def test_unsupported_limits_disable_slider(self):
        gpu = parse_gpus(f"{A}, Example, 0000:00:00.0, N/A, N/A, N/A, N/A, N/A")[0]
        self.assertFalse(gpu.adjustable)

    def test_saved_uuid_survives_device_reordering(self):
        targets, errors = load_targets(list(reversed(parse_gpus(SAMPLE))), {A: 175, B: 45})
        self.assertEqual(targets, {A: 175})
        self.assertEqual(errors, [])

    def test_removed_gpu_and_invalid_limit_are_not_applied(self):
        targets, errors = load_targets(parse_gpus(SAMPLE), {A: 999, "GPU-gone": 200})
        self.assertEqual(targets, {})
        self.assertEqual(len(errors), 2)

    def test_nan_and_out_of_range_are_rejected(self):
        gpu = parse_gpus(SAMPLE)[0]
        for value in (float('nan'), float('inf'), 99, 251, 'no'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gpu.validate(value)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(self.temp.name)

    def test_save_load_and_disable_autostart(self):
        self.settings.save_limits({A: 200})
        self.assertFalse(self.settings.autostart.exists())
        self.settings.set_startup(True)
        self.assertIn('--startup', self.settings.autostart.read_text())
        self.settings.save_limits({B: 40})
        self.settings.set_startup(False)
        self.assertFalse(self.settings.autostart.exists())
        self.assertEqual(self.settings.read()['limits'], {A: 200, B: 40})
        self.assertFalse(self.settings.read()['start_at_login'])
        self.assertEqual(self.settings.path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_config_is_not_overwritten(self):
        self.settings.path.parent.mkdir(parents=True)
        self.settings.path.write_text('not json')
        with self.assertRaises(ValueError):
            self.settings.save_limits({A: 200})
        self.assertEqual(self.settings.path.read_text(), 'not json')

    def test_autostart_failure_rolls_back_flag(self):
        self.settings.save_limits({A: 200})
        self.settings.autostart.parent.mkdir()
        self.settings.autostart.mkdir()
        with self.assertRaises(OSError):
            self.settings.set_startup(True)
        self.assertFalse(self.settings.read()['start_at_login'])

    def test_nonfinite_saved_value_rejected(self):
        self.settings.path.parent.mkdir(parents=True)
        self.settings.path.write_text(json.dumps({'version': 1, 'start_at_login': False, 'limits': {A: float('nan')}}))
        with self.assertRaises(ValueError):
            self.settings.read()
