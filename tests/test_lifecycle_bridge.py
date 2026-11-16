"""Critical recovery ownership and clock regressions; no game interaction."""
from types import SimpleNamespace as NS
import unittest

from test_combat_bridge import MOD, load_definitions


class LifecycleBridgeTests(unittest.TestCase):
    def harness(self, native_state=None):
        calls = []
        clock = [10.0]
        ns = dict(time=NS(monotonic=lambda: clock[0]),
            _active=True, _cfg_assist=True, _cfg_native_precision=True,
            _coyote_enabled=lambda: False,
            _cfg_precision=False, _armed=True, _crosshair_modal_open=False,
            _travel_pending=False, _travel_mode=None, _travel_operation=0,
            _travel_epoch=0, _travel_native_owner=None, _travel_generation=None,
            _travel_ready_ticks=0, _travel_close_ready_ticks=0,
            _travel_initial_rearm_at=0.0, _travel_last_rearm_at=0.0,
            _travel_ready_validated=False, _travel_verified_sprint=False,
            _travel_modal_open=False, _travel_close_pending=False,
            _travel_input=NS(synthetic=False, samples=0, repairs=0,
                physical_seen=set(), no_device_samples=0, last_source='', last_error='',
                reset=lambda: calls.append('input-reset')),
            _precision=NS(reset_travel=lambda: calls.append('native-reset'),
                reset_assist=lambda kind: calls.append(('assist-reset', kind)),
                restore=lambda: calls.append('speed-restore'),
                travel_gate=lambda *a, **kw: calls.append(('gate', a, kw)) or native_state),
            _debug=lambda message: None,
            _set_hook=lambda *a: calls.append(('hook', a[0], a[1])) or True,
            _set_optional_hook=lambda *a: calls.append(('hook', a[0], a[1])) or True,
            _stop_travel_input_handoff=lambda: calls.append('handoff-stop'),
            _local_travel_controller=lambda obj: obj,
            _clear_assist=lambda: calls.append('assist-clear'),
            INPUT_FRAME='input', WALKING_PROCESS_MOVE='move', PLAYER_TICK='tick',
            Type=NS(POST_UNCONDITIONAL=3),
            _travel_input_frame=lambda *a: None,
            _travel_walking_process_move=lambda *a: None)
        load_definitions(MOD / 'recovery_budget.py', ['RecoveryBudget'], ns)
        ns['_travel_budget'] = ns['RecoveryBudget']()
        names = ['_begin_travel_operation', '_reset_travel_readiness', '_travel_readiness',
                 '_start_travel_recovery', '_stop_travel_recovery',
                 '_pre_client_travel', '_world_loaded', '_fast_travel_loaded',
                 '_travel_recovery_tick']
        load_definitions(MOD / '__init__.py', names, ns)
        pc = NS(bInSprintState=False, _get_address=lambda: 10,
                PlayerInput=NS(_get_address=lambda: 20), Pawn=NS(_get_address=lambda: 30))
        ns.update(_local_pc=lambda obj: pc if obj is pc else None,
            _pc=lambda **kw: pc, _same=lambda a, b: a is b,
            _basic_context=lambda *a, **kw: True,
            _stop_assist_recovery=lambda: calls.append('assist-stop'),
            _set_try_sprint=lambda pi, requested: calls.append(('request', requested)),
            _assist_recover_or_schedule=lambda pc: calls.append('recover'),
            _travel_normal_sprint_retry=lambda *a: calls.append('direct-retry'))
        return ns, pc, clock, calls

    def start(self, ns):
        ns['_begin_travel_operation']()
        ns['_start_travel_recovery']()

    def test_duplicate_arrivals_preserve_operation_mode_and_elapsed_budget(self):
        ns, pc, clock, _ = self.harness()
        self.start(ns)
        budget = ns['_travel_budget']
        budget.sample(10, True)
        budget.sample(14, True)
        ns['_armed'] = False
        clock[0] = 15
        ns['_world_loaded'](pc, NS(bFinalDest=True), None, None)
        ns['_fast_travel_loaded'](pc, None, None, None)
        self.assertEqual(ns['_travel_mode'], True)
        self.assertEqual((budget.started, budget.eligible), (10, 4))
        self.assertFalse(budget.ready)

    def test_departure_supersedes_recovery_but_duplicate_departure_does_not(self):
        ns, pc, clock, _ = self.harness()
        self.start(ns)
        ns['_armed'] = False
        ns['_pre_client_travel'](pc, None, None, None)
        self.assertEqual(ns['_travel_budget'].result, 'superseded-by-travel')
        self.assertFalse(ns['_travel_budget'].active)
        self.assertEqual(ns['_travel_mode'], False)
        ns['_pre_client_travel'](pc, None, None, None)
        self.assertFalse(ns['_travel_budget'].active)
        clock[0] = 20
        ns['_world_loaded'](pc, NS(bFinalDest=False), None, None)
        self.assertFalse(ns['_travel_budget'].active)
        ns['_world_loaded'](pc, NS(bFinalDest=True), None, None)
        self.assertEqual(ns['_travel_budget'].started, 20)

    def test_precision_off_uses_native_readiness_without_parallel_python_timer(self):
        ns, pc, _, calls = self.harness((True, True, False))
        self.assertIs(ns['_travel_readiness'](pc, 1), True)
        self.assertTrue(calls[-1][2]['prefer_native'])
        self.assertEqual(ns['_travel_ready_ticks'], 0)
        self.assertTrue(ns['_travel_native_owner'])

    def test_native_failure_handoff_restarts_transients_without_changing_budget(self):
        ns, pc, _, _ = self.harness((False, False, False))
        self.start(ns)
        ns['_travel_readiness'](pc, 1)
        ns['_travel_ready_ticks'] = 3
        ns['_travel_close_ready_ticks'] = 5
        ns['_travel_initial_rearm_at'] = 1
        ns['_travel_verified_sprint'] = True
        ns['_precision'].travel_gate = lambda *a, **kw: None
        self.assertIsNone(ns['_travel_readiness'](pc, 2))
        self.assertEqual(ns['_travel_ready_ticks'], 1)
        self.assertEqual(ns['_travel_close_ready_ticks'], 0)
        self.assertEqual(ns['_travel_initial_rearm_at'], 0)
        self.assertFalse(ns['_travel_verified_sprint'])
        self.assertEqual(ns['_travel_mode'], True)
        self.assertEqual(ns['_travel_budget'].started, 10)

    def test_python_destination_ui_and_retry_boundaries(self):
        ns, pc, _, _ = self.harness()
        ns['_travel_mode'] = True
        ns['_travel_close_pending'] = True
        for i in range(7):
            self.assertIsNone(ns['_travel_readiness'](pc, 1 + i / 256))
        self.assertIs(ns['_travel_readiness'](pc, 1.1), True)
        self.assertFalse(ns['_travel_close_pending'])
        ns['_travel_last_rearm_at'] = 1.1
        self.assertIs(ns['_travel_readiness'](pc, 1.349), False)
        self.assertIs(ns['_travel_readiness'](pc, 1.35), True)

    def test_settle_requires_current_engine_sprint_and_cleans_hooks(self):
        ns, pc, clock, calls = self.harness((True, False, False))
        self.start(ns)
        ns['_travel_generation'] = (10, 20, 30)
        ns['_travel_initial_rearm_at'] = 1
        budget = ns['_travel_budget']
        budget.sample(10, True)
        clock[0] = 14
        ns['_travel_recovery_tick'](pc, None, None, None)
        self.assertTrue(budget.active)
        pc.bInSprintState = True
        ns['_travel_recovery_tick'](pc, None, None, None)
        self.assertEqual(budget.result, 'confirmed-recovery')
        self.assertFalse(ns['_travel_pending'])
        self.assertIn(('hook', 'travel-recovery', False), calls)
        self.assertIn(('hook', 'travel-walking-state', False), calls)
        self.assertIn('handoff-stop', calls)

    def test_timeout_walk_and_disable_cleanup_retains_only_physical_release_helper(self):
        for reason in ('walk-selected', 'overall-timeout', 'eligible-time-exhausted', 'cancelled'):
            with self.subTest(reason=reason):
                ns, _, _, calls = self.harness()
                self.start(ns)
                ns['_travel_input'].synthetic = True
                ns['_stop_travel_recovery'](reason)
                self.assertEqual(ns['_travel_budget'].result, reason)
                self.assertFalse(ns['_travel_pending'])
                self.assertNotIn('handoff-stop', calls)
                ns['_active'] = False
                ns['_stop_travel_recovery']('disabled')
                self.assertIn('handoff-stop', calls)

    def test_shared_high_fps_clock_and_modal_budget_limits(self):
        ns, _, _, _ = self.harness()
        budget = ns['_travel_budget']
        budget.start(0)
        budget.sample(0, True)
        for i in range(1, 24 * 256 + 1):
            budget.sample(i / 256, True)  # PlayerTick
            budget.sample(i / 256, True)  # ProcessMove shares the same clock
        self.assertEqual(budget.eligible, 24)
        self.assertEqual(budget.expiration(24), 'eligible-time-exhausted')
        budget.finish('done')
        budget.start(30)
        budget.sample(30, True)
        budget.sample(31, True)
        budget.sample(32, False)
        budget.sample(100, False)
        budget.sample(101, True)
        self.assertEqual(budget.eligible, 1)
        self.assertEqual(budget.expiration(150), 'overall-timeout')


if __name__ == '__main__':
    unittest.main()
