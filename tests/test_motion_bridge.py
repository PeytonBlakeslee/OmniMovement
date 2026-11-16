"""Production Stage22 boundaries: settings, ownership and failure handoff."""
import math
from types import SimpleNamespace as NS
import unittest

from test_combat_bridge import MOD, load_definitions


class MotionBridgeTests(unittest.TestCase):
    def bridge(self):
        calls = []
        pi = NS(_get_address=lambda: 20)
        pawn = NS(_get_address=lambda: 30)
        pc = NS(_get_address=lambda: 10, PlayerInput=pi, Pawn=pawn)
        native = NS(
            motion_update=lambda *a: calls.append(('motion', a)) or
                (True, (1.0, 0.0, True, True, False, False), (False, False, False), False),
            motion_reset=lambda: calls.append(('motion-reset',)),
            restore=lambda: calls.append(('native-restore',)),
            velocity_write=lambda *a: calls.append(('write', a)) or True)
        ns = dict(math=math, PythonPrecisionSprint=lambda: NS(
            restore=lambda: calls.append(('python-restore',))),
            logging=NS(warning=lambda message: calls.append(('warning',))))
        load_definitions(MOD / 'precision_native_adapter.py', ['PrecisionSprint'], ns)
        adapter = ns['PrecisionSprint']()
        adapter._native = native
        adapter._try_native = lambda: native
        return adapter, native, pc, pi, pawn, calls

    def observe(self, adapter, pc, pi, precision=False):
        return adapter.motion_fused(pc, pi, 4, precision_enabled=precision,
            strength=0.7, center_grace=0.1, center_enabled=True,
            reversal_enabled=True, correction_active=False)

    def test_native_motion_with_precision_disabled_and_no_speed_owner(self):
        adapter, _, pc, pi, _, calls = self.bridge()
        gate, scan, changed = self.observe(adapter, pc, pi)
        self.assertTrue(gate[2])
        self.assertEqual(scan, (False, False, False))
        self.assertFalse(changed)
        self.assertFalse(adapter._native_active)
        self.assertEqual(calls, [('motion', (10, 20, 4, False, 0.7, 0.1, True, True, False))])

    def test_compact_v1_negotiates_without_changing_legacy_or_correction_calls(self):
        adapter, native, pc, pi, _, calls = self.bridge()
        opts = dict(precision_enabled=False, strength=0.7, center_grace=0.1,
                    compact_gate=True)
        native.motion_update = lambda *a: calls.append(('motion', a)) or (
            True, (1.0, 0.0, True, True, False, False), None, False)
        self.assertIsInstance(adapter.motion_fused(pc, pi, 4, **opts), tuple)
        self.assertEqual(calls[0][0], 'motion')
        calls.clear()
        native.motion_gate_v1 = lambda *a: calls.append(('compact', a)) or 7
        self.assertEqual(adapter.motion_fused(pc, pi, 4, **opts), 7)
        self.assertEqual(calls, [('compact', (10, 20, 4, False, 0.7, 0.1))])
        calls.clear()
        self.assertIsInstance(adapter.motion_fused(pc, pi, 4, reversal_enabled=True, **opts), tuple)
        self.assertEqual(calls[0][0], 'motion')

    def test_invalid_compact_gate_restores_then_falls_back_without_repeating_snapshot(self):
        for invalid in (True, -1, 8, 1, 5, (False,)):
            with self.subTest(invalid=invalid):
                adapter, native, pc, pi, _, calls = self.bridge()
                native.motion_gate_v1 = lambda *a: calls.append(('compact',)) or invalid
                self.assertIsNone(adapter.motion_fused(
                    pc, pi, 4, precision_enabled=False, strength=0.7, center_grace=0.1,
                    compact_gate=True))
                self.assertTrue(adapter._native_motion_fusion_failed)
                self.assertEqual(calls[:3], [('compact',), ('native-restore',), ('motion-reset',)])
                self.assertFalse(any(item[0] == 'motion' for item in calls))

    def test_compact_gate_preserves_speed_handoff(self):
        adapter, native, pc, pi, _, calls = self.bridge()
        native.motion_gate_v1 = lambda *a: calls.append(('compact', a[3])) or 0
        opts = dict(strength=0.7, center_grace=0.1, compact_gate=True)
        self.assertEqual(adapter.motion_fused(pc, pi, 4, precision_enabled=True, **opts), 0)
        self.assertTrue(adapter._native_active)
        self.assertEqual(adapter.motion_fused(pc, pi, 4, precision_enabled=False, **opts), 0)
        self.assertFalse(adapter._native_active)
        self.assertEqual(calls, [('python-restore',), ('compact', True),
                                 ('compact', False), ('native-restore',)])

    def test_verified_callback_hints_need_no_pawn_address_or_owner_reread(self):
        adapter, native, _, pi, _, calls = self.bridge()
        native.motion_gate_v1 = lambda *a: calls.append(('compact', a)) or 3
        opts = dict(precision_enabled=False, strength=0.7, center_grace=0.1,
                    compact_gate=True, pawn=object(), owner_input=pi,
                    input_address=20, owner_input_address=20)
        pc = NS(_get_address=lambda: 10)
        self.assertEqual(adapter.motion_fused(pc, pi, 4, **opts), 3)
        self.assertEqual(calls, [('compact', (10, 20, 4, False, 0.7, 0.1))])
        opts['owner_input_address'] = 21
        calls.clear()
        self.assertIsNone(adapter.motion_fused(pc, pi, 4, **opts))
        self.assertEqual(calls, [('motion-reset',)])
        self.assertFalse(adapter._native_motion_fusion_failed)

    def test_precision_transfer_restores_python_before_first_native_write(self):
        adapter, _, pc, pi, _, calls = self.bridge()
        self.observe(adapter, pc, pi, True)
        self.observe(adapter, pc, pi, True)
        self.assertTrue(adapter._native_active)
        self.assertEqual(calls[0], ('python-restore',))
        self.assertEqual(calls.count(('python-restore',)), 1)
        self.observe(adapter, pc, pi, False)
        self.assertFalse(adapter._native_active)
        # The fused backend observes precision-off before the adapter explicitly
        # finalizes native speed ownership. Inspect the motion event itself.
        self.assertEqual(calls[-2][0], 'motion')
        self.assertFalse(calls[-2][1][3])
        self.assertEqual(calls[-1], ('native-restore',))

    def test_precision_off_retains_pending_native_restore(self):
        adapter, native, pc, pi, _, calls = self.bridge()
        self.observe(adapter, pc, pi, True)
        native.restore = lambda: calls.append(('native-restore-pending',)) or False
        self.observe(adapter, pc, pi, False)
        self.assertTrue(adapter._native_active)
        self.assertEqual(calls[-1], ('native-restore-pending',))
        native.restore = lambda: calls.append(('native-restore',)) or True
        self.observe(adapter, pc, pi, False)
        self.assertFalse(adapter._native_active)
        self.assertEqual(calls[-1], ('native-restore',))

    def test_partial_motion_failure_restores_and_resets_before_fallback(self):
        adapter, native, pc, pi, _, calls = self.bridge()
        native.motion_update = lambda *a: (True, (0.0, 0.0, False, False, False, False),
                                           (True, True, False), False)
        self.assertIsNone(self.observe(adapter, pc, pi))
        self.assertTrue(adapter._native_motion_fusion_failed)
        self.assertEqual(calls[:2], [('native-restore',), ('motion-reset',)])
        calls.clear()
        self.assertIsNone(self.observe(adapter, pc, pi))
        self.assertEqual(calls, [])

    def test_missing_pawn_and_replaced_input_do_not_advance_motion(self):
        adapter, _, pc, pi, pawn, calls = self.bridge()
        pc.Pawn = None
        self.assertIsNone(self.observe(adapter, pc, pi))
        pc.Pawn = pawn
        pc.PlayerInput = NS(_get_address=lambda: 21)
        self.assertIsNone(self.observe(adapter, pc, pi))
        self.assertEqual(calls, [('motion-reset',), ('motion-reset',)])
        self.assertFalse(adapter._native_motion_fusion_failed)

    def test_native_writer_is_independent_of_precision_and_rejection_is_terminal(self):
        adapter, native, pc, pi, pawn, calls = self.bridge()
        self.assertFalse(adapter._native_active)
        self.assertTrue(adapter.velocity_write(pc, pi, pawn, 4, -440, 0))
        self.assertEqual(calls, [('write', (10, 20, 30, 4, -440.0, 0.0))])
        native.velocity_write = lambda *a: False
        self.assertIs(adapter.velocity_write(pc, pi, pawn, 4, 1, 0), False)
        self.assertFalse(adapter._native_velocity_failed)
        self.assertIs(adapter.velocity_write(pc, pi, NS(_get_address=lambda: 31), 4, 1, 0), False)
        pc.PlayerInput = NS(_get_address=lambda: 21)
        self.assertIs(adapter.velocity_write(pc, pi, pawn, 4, 1, 0), False)

    def test_explicit_native_prewrite_status_uses_guarded_fallback_once(self):
        adapter, native, pc, pi, pawn, calls = self.bridge()
        native.velocity_write = lambda *args: None
        self.assertIsNone(adapter.velocity_write(pc, pi, pawn, 4, 1, 0))
        self.assertTrue(adapter._native_velocity_failed)
        self.assertIsNone(adapter.velocity_write(pc, pi, pawn, 4, 1, 0))
        self.assertEqual(calls, [('warning',)])

    def test_unclassified_native_exception_cannot_authorize_python_fallback(self):
        adapter, native, pc, pi, pawn, calls = self.bridge()
        def broken(*args):
            pawn.Velocity = NS(X=-200.0, Y=30.0)
            raise RuntimeError('exception after native write')
        native.velocity_write = broken
        self.assertIs(adapter.velocity_write(pc, pi, pawn, 4, -200, 80), False)
        self.assertTrue(adapter._native_velocity_failed)
        self.assertEqual((pawn.Velocity.X,pawn.Velocity.Y),(-200.0,30.0))
        self.assertIsNone(adapter.velocity_write(pc, pi, pawn, 4, -200, 80))
        self.assertEqual(calls, [('warning',)])

    def test_partial_native_velocity_commit_is_terminal_for_current_frame(self):
        adapter, native, pc, pi, pawn, calls = self.bridge()
        pawn.Velocity = NS(X=100.0, Y=40.0, Z=7.0)
        pawn.Physics = 1
        pawn.Controller = pc
        pc.bInSprintState = True
        def partial(*args):
            pawn.Velocity.X = -200.0
            return -1  # New native commit-uncertain sentinel.
        native.velocity_write = partial
        self.assertIs(adapter.velocity_write(pc, pi, pawn, 4, -200, -100), False)
        self.assertTrue(adapter._native_velocity_failed)
        self.assertEqual((pawn.Velocity.X,pawn.Velocity.Y,pawn.Velocity.Z),
                         (-200.0,40.0,7.0))
        self.assertIsNone(adapter.velocity_write(pc, pi, pawn, 4, -200, -100))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "warning")

    def test_native_invalid_response_blocks_same_frame_fallback(self):
        adapter, native, pc, pi, pawn, calls = self.bridge()
        native.velocity_write = lambda *args: ("invalid",)
        self.assertIs(adapter.velocity_write(pc, pi, pawn, 4, -100, 0), False)
        self.assertTrue(adapter._native_velocity_failed)
        self.assertEqual([x[0] for x in calls], ["warning"])

    def test_output_boundary_does_not_repeat_uncertain_native_vector(self):
        ns, adapter, pc, pi, pawn, calls = self.supervisor()
        ns["_basic_context"] = lambda *args: True
        def partial(*args):
            pawn.Velocity.X = -120.0
            return False
        adapter.velocity_write = partial
        self.assertFalse(ns["_write_reversal_velocity"](pawn, -120.0, -80.0))
        self.assertEqual((pawn.Velocity.X, pawn.Velocity.Y, pawn.Velocity.Z),
                         (-120.0,0.0,7.0))
        self.assertTrue(math.isfinite(pawn.Velocity.X))

    def supervisor(self, precision=False, changed=False):
        calls = []
        pi = NS(_get_address=lambda: 20, aBaseY=1.0, aStrafe=0.0)
        pawn = NS(_get_address=lambda: 30, Physics=1, Velocity=NS(X=10.0, Y=0.0, Z=7.0))
        pc = NS(_get_address=lambda: 10, PlayerInput=pi, Pawn=pawn, bInSprintState=True)
        pawn.Controller = pc
        def motion(*args, **kwargs):
            calls.append(('motion', kwargs['precision_enabled']))
            return (1.0, 0.0, True, True, False, False), None, changed
        adapter = NS(motion_fused=motion, update=lambda *a, **kw: calls.append(('split-speed',)),
            gate_update=lambda *a: calls.append(('split-gate',)))
        ns = dict(_active=True, _cfg_omni=True, _pc=lambda: pc, _pi_address=20,
            _same=lambda a, b: a is not None and b is not None and a._get_address() == b._get_address(),
            _cfg_native_precision=True, _cfg_precision=precision, _cfg_precision_strength=0.7,
            _cfg_debug=False, _cfg_gap=0.1, _cfg_direction='Vanilla', VANILLA='Vanilla',
            _gate_sprinting=False, _center_transition_origin=None, _precision=adapter,
            _travel_epoch=4, _cfg_assist=False,
            _reversal=NS(origin=None, reset=lambda: calls.append(('mirror-reset',))),
            _clear_center_transition=lambda: calls.append(('center-reset',)),
            _native_reversal_was_active=False, _reversal_last_at=None,
            _gate_last_moving_at=None, _center_origin_velocity=None,
            _pause_sprint_grace=lambda pc: False, Block='block', math=math)
        load_definitions(MOD / '__init__.py', ['_allows', '_write_reversal_velocity'], ns)
        return ns, adapter, pc, pi, pawn, calls

    def test_player_fused_path_unchanged_by_precision_selection(self):
        for precision in (False, True):
            ns, _, _, pi, _, calls = self.supervisor(precision=precision)
            self.assertEqual(ns['_allows'](pi, None, None, None), ('block', True))
            self.assertEqual(calls.count(('motion', precision)), 1)
            self.assertNotIn(('split-speed',), calls)
            self.assertNotIn(('split-gate',), calls)

    def test_generation_reset_clears_python_mirrors_before_dispatch(self):
        ns, _, _, pi, _, calls = self.supervisor(changed=True)
        self.assertEqual(ns['_allows'](pi, None, None, None), ('block', True))
        self.assertEqual(calls, [('motion', False), ('mirror-reset',), ('center-reset',)])

    def test_compact_gate_preserves_grace_cleanup_and_generation_reset(self):
        for flags, allowed in ((0, False), (2, True), (3, True), (7, True)):
            with self.subTest(flags=flags):
                ns, adapter, _, pi, _, calls = self.supervisor()
                adapter.motion_fused = lambda *a, **kw: calls.append(kw['compact_gate']) or flags
                ns['_center_origin_velocity'] = object()
                ns['_reversal_last_at'] = 1.0
                ns['_reset_reversal_state'] = lambda: calls.append('reversal-reset')
                self.assertEqual(ns['_allows'](pi, None, None, None), ('block', allowed))
                expected = {0: ['reversal-reset'], 7: [('mirror-reset',), ('center-reset',)]}
                self.assertEqual(calls, [True] + expected.get(flags, []))
                if flags == 0:
                    self.assertIsNone(ns['_center_origin_velocity'])
                    self.assertIsNone(ns['_reversal_last_at'])

    def test_remote_or_stale_callback_cannot_feed_local_motion(self):
        ns, _, pc, pi, _, calls = self.supervisor()
        other = NS(_get_address=lambda: 21)
        self.assertIsNone(ns['_allows'](other, None, None, None))
        pc.PlayerInput = other
        self.assertIsNone(ns['_allows'](pi, None, None, None))
        self.assertEqual(calls, [])

    def test_output_boundary_rejects_context_without_python_fallback_write(self):
        ns, adapter, pc, pi, pawn, calls = self.supervisor()
        ns['_basic_context'] = lambda *a: True
        adapter.velocity_write = lambda *a: False
        self.assertFalse(ns['_write_reversal_velocity'](pawn, -440.0, 0.0))
        self.assertEqual((pawn.Velocity.X, pawn.Velocity.Y, pawn.Velocity.Z), (10, 0, 7))
        adapter.velocity_write = lambda *a: None
        self.assertTrue(ns['_write_reversal_velocity'](pawn, -440.0, 0.0))
        self.assertEqual((pawn.Velocity.X, pawn.Velocity.Y, pawn.Velocity.Z), (-440, 0, 7))
        for change in ('airborne', 'menu', 'replacement', 'remote', 'nonfinite'):
            pawn.Velocity.X = 10
            pawn.Physics = 2 if change == 'airborne' else 1
            ns['_basic_context'] = lambda *a: change != 'menu'
            pc.Pawn = NS(_get_address=lambda: 31) if change == 'replacement' else pawn
            pawn.Controller = NS(_get_address=lambda: 11) if change == 'remote' else pc
            vx = float('nan') if change == 'nonfinite' else -440.0
            self.assertFalse(ns['_write_reversal_velocity'](pawn, vx, 0.0))
            self.assertEqual(pawn.Velocity.X, 10)


if __name__ == '__main__':
    unittest.main()
