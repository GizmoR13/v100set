from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'packaging/v100set-power'
loader = SourceFileLoader('power_helper', str(path))
spec = spec_from_loader(loader.name, loader)
helper = module_from_spec(spec)
loader.exec_module(helper)
UUID = 'GPU-11111111-2222-3333-4444-555555555555'


class HelperTests(unittest.TestCase):
    def test_strict_argument_validation(self):
        for args in ([], [UUID], [UUID, '200', 'extra'], ['0', '200'],
                     [UUID, 'NaN'], [UUID, '-1'], [UUID, '200; id'], [UUID, '1e3'],
                     ['../../bin/sh', '200'], [UUID, 'inf']):
            with self.subTest(args=args), self.assertRaises(ValueError):
                helper.validate_request(args)

    def test_out_of_range_never_sets_hardware(self):
        with patch.object(helper, 'run', return_value=f'{UUID}, 100, 250') as run:
            with self.assertRaises(ValueError):
                helper.set_limit([UUID, '251'])
            self.assertEqual(run.call_count, 1)

    def test_wrong_identity_rejected(self):
        with patch.object(helper, 'run', return_value='GPU-other, 100, 250') as run:
            with self.assertRaises(ValueError):
                helper.set_limit([UUID, '200'])
            self.assertEqual(run.call_count, 1)

    def test_valid_set_checks_actual_value(self):
        with patch.object(helper, 'run', side_effect=[f'{UUID}, 100, 250', 'ok', '200.00']) as run:
            self.assertEqual(helper.set_limit([UUID, '200']), 200)
            self.assertEqual(run.call_args_list[1].args, ('-i', UUID, '-pl', '200.0'))

    def test_driver_not_applying_value_is_reported(self):
        with patch.object(helper, 'run', side_effect=[f'{UUID}, 100, 250', 'ok', '250.00']):
            with self.assertRaises(RuntimeError):
                helper.set_limit([UUID, '200'])

    def test_subprocess_uses_fixed_executable_and_environment(self):
        with patch.object(helper.subprocess, 'run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = 'ok'
            helper.run('-i', UUID)
            self.assertEqual(run.call_args.args[0][0], '/usr/bin/nvidia-smi')
            self.assertEqual(run.call_args.kwargs['cwd'], '/')
            self.assertNotIn('shell', run.call_args.kwargs)
            self.assertEqual(run.call_args.kwargs['env'], helper.ENV)
