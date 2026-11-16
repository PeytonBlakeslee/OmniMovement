"""SEC-04/05 safety regression for production native_hipfire.py.

SDK-free. No WinAPI calls or actual memory writes; a deterministic ctypes fake
exercises the production _write/apply/restore methods and ownership ledger.
"""
import ctypes
import importlib.util
import sys
import types
import unittest
from pathlib import Path

sys.modules.setdefault('unrealsdk', types.ModuleType('unrealsdk'))
SOURCE = Path(__file__).resolve().parents[1] / 'mod' / 'OmniMovement' / 'native_hipfire.py'
spec = importlib.util.spec_from_file_location('omni_sec_native_hipfire', SOURCE)
hip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hip)


def script_with_expr(path, *, variant=0):
    script = bytearray(hip.RUNTIME_SIZES[path])
    script[0], script[-1] = 0x0F, 0x53
    prefix_at = 45
    expression_at = prefix_at + len(hip.TARGET_PREFIX)
    script[prefix_at:expression_at] = hip.TARGET_PREFIX
    original = bytearray((0x40 + i + variant) & 0xFF for i in range(hip.RUNTIME_EXPR_LEN))
    for offset, byte in hip.ORIGINAL_FIXED.items():
        original[offset] = byte
    script[expression_at:expression_at+hip.RUNTIME_EXPR_LEN] = original
    suffix_at = expression_at + hip.RUNTIME_EXPR_LEN
    script[suffix_at:suffix_at+len(hip.TARGET_SUFFIX)] = hip.TARGET_SUFFIX
    return script, expression_at, bytes(original)


class FakeWinAPI:
    def __init__(self, script, base):
        self.script, self.base = script, base
        self.protection_calls = 0
        self.writes = 0
        self.flush_calls = 0
        self.fail_initial_protect_at = set()
        self.fail_restore_protect_at = set()
        self.raise_restore_protect_at = set()
        self.write_mode = 'normal'  # fail / partial / normal
        self.fail_flush_at = set()

    def VirtualProtect(self, address, size, new_protection, old_ptr):
        self.protection_calls += 1
        call = self.protection_calls
        if call in self.raise_restore_protect_at:
            raise RuntimeError('mock protect exception')
        if call in self.fail_initial_protect_at | self.fail_restore_protect_at:
            return 0
        ctypes.cast(old_ptr, ctypes.POINTER(ctypes.c_uint32)).contents.value = 0x20
        return 1

    def WriteProcessMemory(self, process, address, buf, size, written_ptr):
        self.writes += 1
        start = address.value - self.base
        payload = ctypes.string_at(buf, size)
        if self.write_mode == 'fail':
            count = 0
        elif self.write_mode == 'partial':
            count = size // 2
        else:
            count = size
        self.script[start:start+count] = payload[:count]
        ctypes.cast(written_ptr, ctypes.POINTER(ctypes.c_size_t)).contents.value = count
        return int(self.write_mode != 'fail')

    def FlushInstructionCache(self, process, address, size):
        self.flush_calls += 1
        return int(self.flush_calls not in self.fail_flush_at)


class PatchWithFakeWindows(hip.NativeHipFirePatch):
    def __init__(self):
        super().__init__()
        self.base = 0x100000
        self.script, self.off, self.original = script_with_expr(hip.CAN_CONTINUE_SPRINTING)
        self.fake = FakeWinAPI(self.script, self.base)
        self._kernel32 = self.fake
        self._process = 1
        self.fail_readback_once = False

    def _init_winapi(self):
        pass

    def _read_function_script(self, path):
        if path != hip.CAN_CONTINUE_SPRINTING:
            raise RuntimeError('unexpected path')
        return self.base, bytes(self.script)

    def _read(self, address, length):
        if self.fail_readback_once:
            self.fail_readback_once = False
            return None
        offset = address - self.base
        return bytes(self.script[offset:offset+length])

    def expression(self):
        return bytes(self.script[self.off:self.off+hip.RUNTIME_EXPR_LEN])


class MatcherTests(unittest.TestCase):
    def test_known_structural_variants_accept(self):
        for variant in (0, 19, 101):
            script, pos, original = script_with_expr(hip.CAN_CONTINUE_SPRINTING, variant=variant)
            self.assertEqual(hip._find_target(bytes(script)), (pos, False))
            self.assertEqual(bytes(script[pos:pos+27]), original)

    def test_unchecked_reference_bytes_remain_unverified(self):
        # Explicit limitation: matching these structural bytes does NOT prove
        # an unfamiliar pointer/FName tuple targets the same game expression.
        script, pos, _ = script_with_expr(hip.CAN_CONTINUE_SPRINTING)
        script[pos + 22] ^= 0x55
        self.assertEqual(hip._find_target(bytes(script)), (pos, False))

    def test_already_patched_is_detected_and_rejected_by_apply(self):
        script, pos, _ = script_with_expr(hip.CAN_CONTINUE_SPRINTING)
        script[pos:pos+27] = hip.PATCH_EXPR
        self.assertEqual(hip._find_target(bytes(script)), (pos, True))
        patch = PatchWithFakeWindows()
        patch.script[patch.off:patch.off+27] = hip.PATCH_EXPR
        self.assertFalse(patch.apply())
        self.assertEqual(patch.fake.writes, 0)

    def test_duplicate_matching_expression_is_ambiguous(self):
        script, pos, _ = script_with_expr(hip.CAN_CONTINUE_SPRINTING)
        extra = 175
        script[extra:extra+len(hip.TARGET_PREFIX)] = hip.TARGET_PREFIX
        start = extra + len(hip.TARGET_PREFIX)
        script[start:start+27] = script[pos:pos+27]
        script[start+27:start+27+len(hip.TARGET_SUFFIX)] = hip.TARGET_SUFFIX
        with self.assertRaises(RuntimeError):
            hip._find_target(bytes(script))

    def test_duplicate_envelope_with_different_expression_is_ambiguous(self):
        # Previously ignored an additional same-size prefix+suffix envelope,
        # allowing the other candidate to be patched without proof of uniqueness.
        script, pos, _ = script_with_expr(hip.CAN_CONTINUE_SPRINTING)
        extra = 175
        script[extra:extra+len(hip.TARGET_PREFIX)] = hip.TARGET_PREFIX
        start = extra+len(hip.TARGET_PREFIX)
        altered = bytearray(script[pos:pos+27]); altered[0] ^= 0x01
        script[start:start+27] = altered
        script[start+27:start+27+len(hip.TARGET_SUFFIX)] = hip.TARGET_SUFFIX
        with self.assertRaisesRegex(RuntimeError, 'ambiguous|expected one'):
            hip._find_target(bytes(script))

    def test_single_envelope_with_invalid_opcode_is_rejected(self):
        script, pos, _ = script_with_expr(hip.CAN_CONTINUE_SPRINTING)
        script[pos+17] = 0x80
        with self.assertRaises(RuntimeError):
            hip._find_target(bytes(script))

    def test_malformed_truncated_envelope_rejected(self):
        script, pos, _ = script_with_expr(hip.CAN_CONTINUE_SPRINTING)
        script[45:45+len(hip.TARGET_PREFIX)] = b'\x00'*len(hip.TARGET_PREFIX)
        with self.assertRaises(RuntimeError):
            hip._find_target(bytes(script))


class MemoryProtectionTests(unittest.TestCase):
    def test_normal_apply_and_restore(self):
        patch = PatchWithFakeWindows()
        self.assertTrue(patch.apply())
        self.assertEqual(patch.expression(), hip.PATCH_EXPR)
        self.assertTrue(patch.restore())
        self.assertEqual(patch.expression(), patch.original)
        self.assertEqual(patch.fake.protection_calls, 4)
        self.assertEqual(patch.fake.flush_calls, 2)

    def test_initial_protect_failure_never_writes(self):
        patch = PatchWithFakeWindows()
        patch.fake.fail_initial_protect_at.add(1)
        self.assertFalse(patch.apply())
        self.assertEqual(patch.fake.writes, 0)
        self.assertEqual(patch.expression(), patch.original)
        self.assertFalse(getattr(patch, '_protection_uncertain', False))

    def test_write_failure_before_mutation_rolls_back_without_write(self):
        patch = PatchWithFakeWindows()
        patch.fake.write_mode = 'fail'
        self.assertFalse(patch.apply())
        self.assertEqual(patch.expression(), patch.original)
        self.assertEqual(patch.fake.writes, 1)
        self.assertFalse(patch._owned)

    def test_partial_write_retains_owner_and_never_blindly_repairs(self):
        patch = PatchWithFakeWindows()
        patch.fake.write_mode = 'partial'
        self.assertFalse(patch.apply())
        self.assertNotEqual(patch.expression(), patch.original)
        self.assertNotEqual(patch.expression(), hip.PATCH_EXPR)
        self.assertEqual(patch.fake.writes, 1)
        self.assertTrue(patch._owned)
        self.assertFalse(patch.restore())
        self.assertEqual(patch.fake.writes, 1)
        self.assertIn('partial', patch.last_error.lower())

    def test_readback_failure_rolls_back_owned_write(self):
        patch = PatchWithFakeWindows()
        patch.fail_readback_once = True
        self.assertFalse(patch.apply())
        self.assertEqual(patch.expression(), patch.original)
        self.assertEqual(patch.fake.writes, 2)
        self.assertFalse(patch._owned)

    def test_cache_flush_failure_not_silent_and_rollback_possible(self):
        patch = PatchWithFakeWindows()
        patch.fake.fail_flush_at.add(1)
        self.assertFalse(patch.apply())
        self.assertEqual(patch.expression(), patch.original)
        self.assertIn('FlushInstructionCache', patch.last_error)
        self.assertEqual(patch.fake.writes, 2)
        self.assertEqual(patch.fake.protection_calls, 4)

    def test_final_protect_failure_locks_instance_and_preserves_owner(self):
        patch = PatchWithFakeWindows()
        patch.fake.fail_restore_protect_at.add(2)
        self.assertFalse(patch.apply())
        self.assertEqual(patch.expression(), hip.PATCH_EXPR)
        self.assertTrue(patch._owned)
        self.assertIn('restart required', patch.last_error.lower())
        self.assertFalse(patch.restore())
        self.assertFalse(patch.apply())
        self.assertEqual(patch.fake.writes, 1)
        self.assertEqual(patch.fake.protection_calls, 2)

    def test_final_protect_raises_locks_instance(self):
        patch = PatchWithFakeWindows()
        patch.fake.raise_restore_protect_at.add(2)
        self.assertFalse(patch.apply())
        self.assertTrue(patch._owned)
        self.assertIn('restart required', patch.last_error.lower())
        self.assertFalse(patch.restore())
        self.assertEqual(patch.fake.writes, 1)

    def test_restore_protection_failure_keeps_owner(self):
        patch = PatchWithFakeWindows()
        self.assertTrue(patch.apply())
        patch.fake.fail_restore_protect_at.add(4)
        self.assertFalse(patch.restore())
        self.assertTrue(patch._owned)
        self.assertIn('restart required', patch.last_error.lower())
        self.assertFalse(patch.apply())
        self.assertEqual(patch.fake.writes, 2)

    def test_foreign_mutation_refuses_write_and_preserves_owner(self):
        patch = PatchWithFakeWindows()
        self.assertTrue(patch.apply())
        patch.script[patch.off+10] ^= 0x7F
        self.assertFalse(patch.restore())
        self.assertTrue(patch._owned)
        self.assertEqual(patch.fake.writes, 1)


if __name__ == '__main__':
    unittest.main()
