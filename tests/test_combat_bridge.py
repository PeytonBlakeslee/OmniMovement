"""Focused Stage20 supervisor tests; no game process or other mod is touched."""
import __future__
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

MOD = Path(__file__).resolve().parents[1] / 'mod' / 'OmniMovement'


def load_definitions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    selected = [node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    assert {node.name for node in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec',
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace


class CombatBridgeTests(unittest.TestCase):
    def adapter(self, snapshot, status=4):
        calls = []
        native = NS(combat_snapshot=lambda *args: snapshot,
                    combat_retry_observe=lambda *args: (status, snapshot),
                    assist_reset=lambda which: calls.append(('reset', which)))
        ns = dict(PythonPrecisionSprint=lambda: NS(),
                  logging=NS(warning=lambda text: calls.append(('warning', text))))
        load_definitions(MOD / 'precision_native_adapter.py', ['PrecisionSprint'], ns)
        adapter = ns['PrecisionSprint']()
        adapter._native = native
        adapter._try_native = lambda: native
        pc = NS(_get_address=lambda: 10, Pawn=NS())
        pi = NS(_get_address=lambda: 20)
        return adapter, pc, pi, calls

    def supervisor(self, snapshot=None, status=4, can_sprint=True):
        calls = []
        weapon = NS(bWeaponPutDown=False, ZoomState=0,
                    IsEquipping=lambda: False, IsFiring=lambda: False)
        pawn = NS(Weapon=weapon, OffHandWeapon=None)
        pawn.CanSprint = lambda: calls.append('CanSprint') or can_sprint
        pi = NS(aBaseY=1.0, aStrafe=0.0)
        pc = NS(Pawn=pawn, PlayerInput=pi, bInSprintState=False,
                bThrowingGrenade=False, bPerformingSharedWeaponAction=False,
                bPerformingMeleeAttack=False, bPendingZoom=False, IsZoomed=lambda: False)
        precision = NS(combat_observe=lambda *args, **kwargs:
                           None if snapshot is None else (status, snapshot),
                       assist_snapshot=lambda *args, **kwargs: None,
                       retry_preflight_blocked=lambda *args, **kwargs: None,
                       assist_ready=lambda *args, **kwargs: None,
                       intent_observe=lambda *args, **kwargs: None,
                       reset_assist=lambda kind: calls.append(('reset', kind)))
        ns = dict(_active=True, _cfg_assist=True, _armed=True,
                  _cfg_native_precision=True, _cfg_precision=False,
                  _deathtrap_skill_active=False, _weapon_transition_addresses=set(),
                  _weapon_state_events_complete=True, _precision=precision,
                  _assist_recovery_ready_frames=0,
                  _hooks={"assist-recovery": ("PlayerTick", None, "OmniMovement/assist-recovery")},
                  _hook_diagnostics_seen=set(),
                  _travel_epoch=0,
                  _native_assist_recovery=lambda pc, pi, **kwargs: False,
                  _basic_context=lambda *args, **kwargs: True,
                  _local_pc=lambda obj: pc,
                  _same=lambda a, b: a is b,
                  _assist_raw_moving=lambda pc, **kwargs: True,
                  _stop_assist_recovery=lambda: calls.append('stop'),
                  _set_try_sprint=lambda pi, value: calls.append(('try', value)),
                  _schedule_deathtrap_recovery=lambda pc: calls.append('summon'))
        # These focused supervisors execute isolated production callbacks.
        # Scope the new helper to their existing mocked ordinary on-foot gate;
        # the dedicated Scorpio tests execute the real helper independently.
        ns['_assist_context'] = lambda *args, **kwargs: ns['_basic_context'](*args, **kwargs)
        ns['_skill_assist_context'] = lambda *args, **kwargs: False
        ns['_assist_crouching'] = lambda pc, pawn=None: False
        ns['_crouch_sprint_paused'] = lambda: False
        ns['_zero_execute_waiting'] = lambda: False
        ns['_zero_execute_seed_pending'] = None
        # Normal combat/intent unit tests do not have an active Execute.
        ns['_zero_execute_waiting_cleanup'] = None
        ns['_zero_execute_early_ready'] = False
        ns['_has_weapon_transition'] = lambda pc: bool(ns['_weapon_transition_addresses'])
        load_definitions(MOD / 'interruptions.py', ['action_interrupted'], ns)
        load_definitions(MOD / '__init__.py', ['_interrupted', '_fast_zoomed',
                         '_assist_can_retry', '_assist_recovery_tick', '_weapons_still_firing'], ns)
        return ns, pc, calls

    def test_read_only_backend_works_without_precision_owner(self):
        snapshot = (False, False, False, True, False, False, False, False)
        adapter, pc, pi, _ = self.adapter(snapshot)
        self.assertFalse(adapter._native_active)
        self.assertEqual(adapter.combat_observe(pc, pi, retry=True, prefer_native=True),
                         (4, snapshot))
        self.assertIsNone(adapter.combat_observe(pc, pi, prefer_native=False))

    def test_recovery_snapshot_shape_and_legacy_fallback(self):
        full = (False, False, False, True, False, False, True, True)
        adapter, pc, pi, calls = self.adapter(full)
        self.assertEqual(adapter.combat_observe(pc, pi, prefer_native=True,
                                                recovery_only=True), (None, full))
        adapter._native.assist_combat_v1 = lambda *args: full[:6]
        self.assertEqual(adapter.combat_observe(pc, pi, prefer_native=True,
                                                recovery_only=True), (None, full[:6]))
        self.assertEqual(adapter.combat_observe(pc, pi, prefer_native=True), (None, full))
        adapter._native.assist_combat_v1 = lambda *args: (False,) * 5
        self.assertIsNone(adapter.combat_observe(pc, pi, prefer_native=True,
                                                recovery_only=True))
        self.assertTrue(adapter._native_combat_failed)
        self.assertIn(('reset', 1), calls)

    def test_bad_native_result_falls_back_once_and_resets_debounce(self):
        for snapshot in (None, (False,) * 4, (0,) * 8,
                         (False, True, False, True, False, False, False, False)):
            adapter, pc, pi, calls = self.adapter(snapshot)
            self.assertIsNone(adapter.combat_observe(pc, pi, retry=True, prefer_native=True))
            self.assertTrue(adapter._native_combat_failed)
            self.assertIn(('reset', 1), calls)
            count = len(calls)
            self.assertIsNone(adapter.combat_observe(pc, pi, retry=True, prefer_native=True))
            self.assertEqual(len(calls), count)

    def test_native_candidate_cannot_override_engine_denial(self):
        snapshot = (False, False, False, True, False, False, False, False)
        ns, pc, calls = self.supervisor(snapshot, can_sprint=False)
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertIn('CanSprint', calls)
        self.assertIn(('try', False), calls)
        self.assertNotIn(('try', True), calls)

    def test_native_candidate_keeps_watcher_until_actual_sprint(self):
        snapshot = (False, False, False, True, False, False, False, False)
        ns, pc, calls = self.supervisor(snapshot)
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertIn(('try', True), calls)
        self.assertNotIn('stop', calls)

    def test_action_putdown_zoom_and_transition_block_candidates(self):
        for index in (1, 2, 4, 5):
            snapshot = [False, False, False, True, False, False, False, False]
            snapshot[index] = True
            ns, pc, calls = self.supervisor(tuple(snapshot))
            self.assertFalse(ns['_assist_can_retry'](pc, native_snapshot=tuple(snapshot)))
            self.assertNotIn('CanSprint', calls)
        ns, pc, calls = self.supervisor()
        ns['_weapon_transition_addresses'].add(123)
        self.assertFalse(ns['_assist_can_retry'](pc))

    def test_missing_equipping_events_keep_overridden_function_guard(self):
        snapshot = (False, False, False, True, False, False, False, False)
        ns, pc, _ = self.supervisor(snapshot)
        ns['_weapon_state_events_complete'] = False
        pc.Pawn.Weapon.IsEquipping = lambda: True
        self.assertTrue(ns['_interrupted'](pc))
        self.assertFalse(ns['_assist_can_retry'](pc, native_snapshot=snapshot))

    def test_python_fallback_retains_two_frame_retry(self):
        ns, pc, calls = self.supervisor()
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertNotIn(('try', True), calls)
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertIn(('try', True), calls)
        self.assertEqual(calls.count('CanSprint'), 2)

    def test_stationary_pending_intent_and_confirmed_sprint_are_distinct(self):
        for status, snapshot, expected in (
            (0, (True, False, False, True, False, False, False, False), False),
            (1, (False, False, False, False, False, False, False, False), True),
        ):
            ns, pc, calls = self.supervisor(snapshot, status)
            ns['_assist_recovery_tick'](pc, None, None, None)
            self.assertIn('stop', calls)
            self.assertEqual(('try', True) in calls, expected)

    def test_skill_restriction_is_checked_before_sprint_request(self):
        snapshot = (False, False, False, True, False, False, False, False)
        ns, pc, calls = self.supervisor(snapshot)
        ns['_deathtrap_skill_active'] = True
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertIn('summon', calls)
        self.assertNotIn(('try', True), calls)

    def test_actual_primary_and_offhand_functions_remain_authoritative(self):
        ns, pc, _ = self.supervisor()
        pc.Pawn.OffHandWeapon = NS(IsFiring=lambda: True)
        self.assertTrue(ns['_weapons_still_firing'](pc))
        pc.Pawn.OffHandWeapon.IsFiring = lambda: False
        self.assertFalse(ns['_weapons_still_firing'](pc))
        def unavailable(): raise RuntimeError('state unavailable')
        pc.Pawn.OffHandWeapon.IsFiring = unavailable
        self.assertTrue(ns['_weapons_still_firing'](pc))


if __name__ == '__main__':
    unittest.main()
