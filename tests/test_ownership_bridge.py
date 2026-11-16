"""Fault injection for patch ownership and truthful optional diagnostics."""
import ast
from pathlib import Path
import struct
from types import SimpleNamespace as NS
import unittest

from test_combat_bridge import MOD, load_definitions


class OwnershipBridgeTests(unittest.TestCase):
    def patch(self):
        ns = dict(struct=struct)
        tree = ast.parse((MOD / 'native_hipfire.py').read_text(encoding='utf-8-sig'))
        constants = [n for n in tree.body if isinstance(n, (ast.Assign, ast.Assert))]
        exec(compile(ast.Module(body=constants, type_ignores=[]), 'hipfire-constants', 'exec'), ns)
        load_definitions(MOD / 'native_hipfire.py',
            ['_matches_sparse', '_find_target', 'NativeHipFirePatch'], ns)
        patch = ns['NativeHipFirePatch']()
        original = bytearray(27)
        for offset, value in ns['ORIGINAL_FIXED'].items():
            original[offset] = value
        original = bytes(original)
        memory = [original]
        writes = []
        patch._init_winapi = lambda: None
        patch._locate_all = lambda: [('function', 100, original, 0, False)]
        patch._read_function_script = lambda path: (100, memory[0])
        patch._read = lambda address, size: memory[0]

        def write(address, data):
            self.assertTrue(patch._owned, 'ownership must precede the write')
            writes.append(data)
            memory[0] = data

        patch._write = write
        return patch, ns['PATCH_EXPR'], original, memory, writes

    def test_repeated_apply_preserves_ownership_and_restore_is_exact(self):
        patch, expected, original, memory, writes = self.patch()
        self.assertTrue(patch.apply())
        self.assertTrue(patch.apply())
        self.assertEqual(writes, [expected])
        self.assertEqual(len(patch._owned), 1)
        self.assertTrue(patch.restore())
        self.assertEqual(memory[0], original)
        self.assertFalse(patch.patched)
        self.assertEqual(patch._owned, [])

    def test_failed_verification_rolls_back_a_recorded_write(self):
        patch, expected, original, memory, writes = self.patch()
        reads = [0]

        def read(address, size):
            reads[0] += 1
            return None if reads[0] == 1 else memory[0]

        patch._read = read
        self.assertFalse(patch.apply())
        self.assertEqual(writes, [expected, original])
        self.assertEqual(memory[0], original)
        self.assertEqual(patch._owned, [])

    def test_restore_refuses_replaced_script_and_keeps_record_for_retry(self):
        patch, _, original, memory, writes = self.patch()
        self.assertTrue(patch.apply())
        patch._read_function_script = lambda path: (200, memory[0])
        self.assertFalse(patch.restore())
        self.assertEqual(len(writes), 1)
        self.assertEqual(len(patch._owned), 1)
        patch._read_function_script = lambda path: (100, memory[0])
        self.assertTrue(patch.restore())
        self.assertEqual(memory[0], original)

    def test_failed_rollback_retains_ownership_and_never_overwrites_foreign_bytes(self):
        patch, _, _, memory, writes = self.patch()

        def failed_write(address, data):
            writes.append(data)
            memory[0] = bytes([0xEE]) * 27
            raise RuntimeError('injected write failure')

        patch._write = failed_write
        self.assertFalse(patch.apply())
        self.assertEqual(len(patch._owned), 1)
        self.assertFalse(patch.patched)
        self.assertFalse(patch.apply())
        self.assertFalse(patch.restore())
        self.assertEqual(len(writes), 1)

    def test_accuracy_duplicate_suppression_restores_once(self):
        calls = []
        modifier = NS(Type=2, Value=1.2)
        context = object()
        attr = NS(Name='Sprint', _path_name=lambda: 'D_Attributes.AccuracyResourcePool.Sprint',
                  AddAttributeModifier=lambda **kw: calls.append(kw))
        pc = NS(AccuracyPool=NS(Data=context), bInSprintState=True,
                SprintModifiers=[NS(Modifier=modifier, Context=context, Attribute=attr)])
        ns = dict(_active=True, BASELINE_AVAILABLE=True, _cfg_baseline=True,
            _restoring=False, _sprint_depth=1, _suppressed=[], _accuracy_rules={'Sprint': 1.2},
            _pc=lambda: pc, _same=lambda a, b: a is b, Block=object(),
            unrealsdk=NS(unreal=NS(WeakPointer=lambda value: lambda: value),
                hooks=NS(prevent_hooking_direct_calls=__import__('contextlib').nullcontext)))
        load_definitions(MOD / '__init__.py', ['_accuracy_modifier', '_restore_baseline'], ns)
        args = NS(Context=context, Modifier=modifier)
        ns['_accuracy_modifier'](attr, args, None, None)
        ns['_accuracy_modifier'](attr, args, None, None)
        self.assertEqual(len(ns['_suppressed']), 1)
        ns['_restore_baseline']()
        self.assertEqual(len(calls), 1)
        self.assertEqual(ns['_suppressed'], [])

    def adapter(self):
        calls = []
        ns = dict(PythonPrecisionSprint=lambda: NS(),
            logging=NS(warning=lambda message: calls.append('warning')))
        load_definitions(MOD / 'precision_native_adapter.py', ['PrecisionSprint'], ns)
        adapter = ns['PrecisionSprint']()
        native = NS(travel_step=lambda *args: (True, True, False),
            travel_reset=lambda: calls.append('reset'))
        adapter._native = native
        adapter._try_native = lambda: native
        return adapter, native, calls

    def test_travel_bridge_precision_off_and_malformed_result_fail_closed(self):
        adapter, native, calls = self.adapter()
        kw = dict(ready=True, modal_open=False, close_pending=False,
                  sprinting=False, armed=True, now=1, prefer_native=True)
        self.assertFalse(adapter._native_active)
        self.assertEqual(adapter.travel_gate(1, **kw), (True, True, False))
        native.travel_step = lambda *args: (False, True, False)
        self.assertIsNone(adapter.travel_gate(1, **kw))
        self.assertEqual(calls, ['reset', 'warning'])
        calls.clear()
        self.assertIsNone(adapter.travel_gate(1, **kw))
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
