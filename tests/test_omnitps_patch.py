"""Offline regression tests for the opt-in sprint-entry bytecode owner.

Run with: python -m unittest discover -s tests -p 'test_omnitps_patch.py'
No BL2 runtime, memory modification, or external dependency is required.
"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path

sys.modules.setdefault('unrealsdk', types.ModuleType('unrealsdk'))
FILE = Path(__file__).resolve().parents[1] / 'mod' / 'OmniMovement' / 'native_hipfire.py'
spec = importlib.util.spec_from_file_location('omnitps_hipfire_under_test', FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_script(path):
    script = bytearray(module.RUNTIME_SIZES[path])
    script[0], script[-1] = 0x0F, 0x53
    prefix = 40
    expression = prefix + len(module.TARGET_PREFIX)
    script[prefix:expression] = module.TARGET_PREFIX
    expr = bytearray(module.RUNTIME_EXPR_LEN)
    for index, value in module.ORIGINAL_FIXED.items():
        expr[index] = value
    script[expression: expression + len(expr)] = expr
    suffix = expression + len(expr)
    script[suffix:suffix + len(module.TARGET_SUFFIX)] = module.TARGET_SUFFIX
    return script, expression


class FakeNativePatch(module.NativeHipFirePatch):
    def __init__(self):
        super().__init__()
        self.scripts = {}
        self.positions = {}
        self.fail_write_path = None
        for index, path in enumerate((module.CAN_CONTINUE_SPRINTING, module.CAN_SPRINT)):
            script, position = make_script(path)
            self.scripts[path] = (0x100000 + index * 0x1000, script)
            self.positions[path] = position

    def _init_winapi(self):
        pass

    def _read_function_script(self, path):
        ptr, data = self.scripts[path]
        return ptr, bytes(data)

    def _target(self, address, length):
        for path, (ptr, script) in self.scripts.items():
            off = address - ptr
            if 0 <= off and off + length <= len(script):
                return path, script, off
        raise AssertionError('unexpected native address')

    def _read(self, address, length):
        _, script, offset = self._target(address, length)
        return bytes(script[offset:offset + length])

    def _write(self, address, data):
        path, script, offset = self._target(address, len(data))
        if path == self.fail_write_path:
            raise RuntimeError('injected-write-failure')
        script[offset:offset + len(data)] = data

    def expression(self, path):
        pos = self.positions[path]
        return bytes(self.scripts[path][1][pos:pos + module.RUNTIME_EXPR_LEN])


class TestSprintEntryPatch(unittest.TestCase):
    def setUp(self):
        self.patch = FakeNativePatch()
        self.original = {p: self.patch.expression(p) for p in self.patch.scripts}

    def test_stable_changes_continuation_only(self):
        self.assertTrue(self.patch.apply())
        self.assertTrue(self.patch.patched)
        self.assertFalse(self.patch.start_while_firing)
        self.assertEqual(self.patch.expression(module.CAN_CONTINUE_SPRINTING), module.PATCH_EXPR)
        self.assertEqual(self.patch.expression(module.CAN_SPRINT), self.original[module.CAN_SPRINT])
        self.assertTrue(self.patch.restore())
        self.assertEqual({p: self.patch.expression(p) for p in self.original}, self.original)

    def test_experimental_changes_both_and_restores(self):
        self.assertTrue(self.patch.apply(start_while_firing=True))
        self.assertTrue(self.patch.start_while_firing)
        for path in self.original:
            self.assertEqual(self.patch.expression(path), module.PATCH_EXPR)
        self.assertTrue(self.patch.restore())
        self.assertFalse(self.patch.start_while_firing)
        self.assertEqual({p: self.patch.expression(p) for p in self.original}, self.original)

    def test_mode_change_requires_restore(self):
        self.assertTrue(self.patch.apply())
        self.assertFalse(self.patch.apply(start_while_firing=True))
        self.assertEqual(self.patch.expression(module.CAN_SPRINT), self.original[module.CAN_SPRINT])
        self.assertTrue(self.patch.restore())
        self.assertTrue(self.patch.apply(start_while_firing=True))

    def test_second_write_failure_rolls_back_first(self):
        self.patch.fail_write_path = module.CAN_SPRINT
        self.assertFalse(self.patch.apply(start_while_firing=True))
        self.assertFalse(self.patch.patched)
        self.assertEqual({p: self.patch.expression(p) for p in self.original}, self.original)

    def test_signature_mismatch_does_not_modify_either(self):
        self.patch.scripts[module.CAN_SPRINT][1][40] ^= 0xFF
        before = {p: self.patch.expression(p) for p in self.original}
        self.assertFalse(self.patch.apply(start_while_firing=True))
        self.assertEqual({p: self.patch.expression(p) for p in self.original}, before)

    def test_foreign_mutation_refuses_restore(self):
        self.assertTrue(self.patch.apply(start_while_firing=True))
        path = module.CAN_SPRINT
        pos = self.patch.positions[path]
        self.patch.scripts[path][1][pos + 5] ^= 0x02
        self.assertFalse(self.patch.restore())
        self.assertNotEqual(self.patch.expression(path), self.original[path])
        self.assertTrue(self.patch._owned)


if __name__ == '__main__':
    unittest.main()
