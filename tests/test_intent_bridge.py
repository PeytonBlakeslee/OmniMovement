"""Exercise production intent adapter/supervisor boundaries without SDK writes."""
from contextlib import nullcontext
from types import SimpleNamespace as NS
import unittest

import test_combat_bridge as combat
from test_combat_bridge import MOD, load_definitions


class IntentBridgeTests(unittest.TestCase):
    def setup_bridge(self, response=None, can_sprint=True):
        ns, pc, calls = combat.CombatBridgeTests().supervisor(can_sprint=can_sprint)
        pi, pawn = pc.PlayerInput, pc.Pawn
        pc._get_address = lambda: 10
        pi._get_address = lambda: 20
        pawn._get_address = lambda: 30
        pawn.Physics = 1
        pi.bTryToSprint = False
        pc.bWantsToSprint = False
        native_calls = []
        native = NS(
            combat_snapshot=lambda *args: (pc.bInSprintState, False, False,
                                           bool(pi.aBaseY or pi.aStrafe), False, False, False, False),
            intent_observe=lambda *args: native_calls.append(args) or response,
            intent_press=lambda *args: (not args[-1], True),
            intent_release=lambda *args: native_calls.append(('release', args)),
            intent_reset=lambda mode: native_calls.append(('reset', mode)),
            assist_reset=lambda which: None)
        adapter_ns = dict(PythonPrecisionSprint=lambda: NS(),
                          logging=NS(warning=lambda message: calls.append('warning')))
        load_definitions(MOD / 'precision_native_adapter.py', ['PrecisionSprint'], adapter_ns)
        adapter = adapter_ns['PrecisionSprint']()
        adapter._native = native
        adapter._try_native = lambda: native
        ns.update(_precision=adapter, _travel_epoch=1, _cfg_debug=False,
                   _skill_assist_context=lambda *a, **kw: False,
                   _crouch_sprint_paused=lambda: False,
                   _zero_execute_waiting=lambda: False,
                   _zero_execute_seed_pending=None,
                  _hold_assist_through_airborne=lambda pc: calls.append('airborne'))
        load_definitions(MOD / '__init__.py', ['_native_assist_recovery'], ns)
        return ns, pc, calls, adapter, native, native_calls

    def test_recovery_snapshot_preserves_blocked_and_first_eligible_frames(self):
        for compact in (False, True):
            with self.subTest(compact=compact):
                ns, pc, calls, _, native, _ = self.setup_bridge()
                ready = 0

                def observe(*args):
                    nonlocal ready
                    _, _, _, _, armed, context, sprinting, moving, grounded, permitted, pending, _ = args
                    self.assertTrue(context and not sprinting and moving and grounded)
                    ready = ready + 1 if permitted else 0
                    phase = 4 if not permitted else 5 if ready == 1 else 6
                    request = phase == 6
                    return phase, armed, request, request != pending, False

                def snapshot(*args):
                    calls.append('snapshot')
                    return (False, pc.bPerformingMeleeAttack, pc.bPendingZoom, True,
                            pc.Pawn.Weapon.bWeaponPutDown, False)

                native.intent_observe = observe
                native.combat_snapshot = lambda *args: snapshot() + (False, False)
                if compact:
                    native.assist_combat_v1 = snapshot
                    native.combat_snapshot = lambda *args: self.fail('full snapshot unexpectedly used')
                tick = ns['_assist_recovery_tick']

                tick(pc, None, None, None)  # first eligible, then a blocker resets debounce
                pc.bPerformingMeleeAttack = True
                tick(pc, None, None, None)
                pc.bPerformingMeleeAttack = False
                pc.bPendingZoom = True
                tick(pc, None, None, None)
                pc.bPendingZoom = False
                pc.Pawn.Weapon.bWeaponPutDown = True
                tick(pc, None, None, None)
                pc.Pawn.Weapon.bWeaponPutDown = False
                tick(pc, None, None, None)
                self.assertNotIn(('try', True), calls)
                tick(pc, None, None, None)
                self.assertEqual(calls.count('snapshot'), 6)
                self.assertEqual(calls.count('CanSprint'), 3)
                self.assertEqual(calls[-2:], ['CanSprint', ('try', True)])

    def test_engine_denial_rejects_a_native_retry_request(self):
        ns, pc, calls, adapter, _, observations = self.setup_bridge(
            (6, True, True, True, False), can_sprint=False)
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertTrue(adapter._native_intent_failed)
        self.assertFalse(observations[0][-3])  # Current CanSprint permission.
        self.assertNotIn(('try', True), calls)

    def test_native_omni_intent_distinguishes_swap_from_action_blocker(self):
        ns, pc, calls, _, native, _ = self.setup_bridge()
        ns['_cfg_omni'] = True
        ns['_weapon_transition_addresses'].add(123)
        pc.Pawn.Weapon.bWeaponPutDown = True
        native.assist_combat_v1 = lambda *args: (False, pc.bPerformingMeleeAttack, False,
                                                True, True, False)

        def observe(*args):
            permitted = args[-3]
            return (6 if permitted else 4), True, permitted, permitted, False

        native.intent_observe = observe
        tick = ns['_assist_recovery_tick']
        tick(pc, None, None, None)
        self.assertEqual(calls[-2:], ['CanSprint', ('try', True)])
        pc.bPerformingMeleeAttack = True
        tick(pc, None, None, None)
        self.assertEqual(calls.count(('try', True)), 1)
        self.assertEqual(calls.count('CanSprint'), 1)

    def test_retry_request_stays_pending_until_actual_sprint(self):
        ns, pc, calls, adapter, native, observations = self.setup_bridge((6, True, True, True, False))
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertIn(('try', True), calls)
        self.assertNotIn('stop', calls)
        pc.PlayerInput.bTryToSprint = True
        native.intent_observe = lambda *args: (6, True, True, False, False)
        calls.clear()
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertNotIn(('try', True), calls)  # No duplicate request write.
        self.assertNotIn('stop', calls)
        pc.bInSprintState = True
        native.intent_observe = lambda *args: (2, True, False, True, False)
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertIn('stop', calls)

    def test_stationary_invalid_context_cannot_arm_request(self):
        ns, pc, calls, adapter, native, _ = self.setup_bridge((1, True, False, False, False))
        pc.PlayerInput.aBaseY = 0
        ns['_basic_context'] = lambda *args, **kwargs: False
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertNotIn(('try', True), calls)
        self.assertFalse(adapter._native_intent_failed)
        native.intent_observe = lambda *args: (3, True, True, True, False)
        self.assertIsNone(adapter.intent_observe(pc, pc.PlayerInput, 1, True,
            context_ready=False, sprinting=False, moving=False,
            grounded=True, permitted=False, prefer_native=True))
        self.assertTrue(adapter._native_intent_failed)  # Reject idle on invalid context.
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertNotIn(('try', True), calls)

    def test_missing_pawn_is_temporary_and_replaced_input_is_rejected(self):
        ns, pc, _, adapter, _, observations = self.setup_bridge((5, True, False, False, False))
        saved_pawn, saved_pi = pc.Pawn, pc.PlayerInput
        pc.Pawn = None
        self.assertIsNone(adapter.combat_observe(pc, saved_pi, prefer_native=True))
        self.assertFalse(adapter._native_combat_failed)
        self.assertFalse(ns['_native_assist_recovery'](pc, saved_pi))
        pc.Pawn = saved_pawn
        pc.PlayerInput = NS(_get_address=lambda: 200)
        self.assertIsNone(adapter.intent_press(pc, saved_pi, 1, True, prefer_native=True))
        self.assertFalse(adapter._native_intent_failed)

    def test_unavailable_intent_uses_complete_two_frame_python_recovery(self):
        ns, pc, calls, adapter, native, _ = self.setup_bridge(None)
        # Native intent failure must leave the selected mode in Python.
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertTrue(adapter._native_intent_failed)
        self.assertTrue(ns['_armed'])
        self.assertNotIn(('try', True), calls)
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertIn(('try', True), calls)

    def test_selector_off_uses_python_without_native_observations(self):
        ns, pc, calls, _, _, observations = self.setup_bridge(None)
        ns['_cfg_native_precision'] = False
        ns['_assist_recovery_tick'](pc, None, None, None)
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertEqual(observations, [])
        self.assertIn(('try', True), calls)

    def test_airborne_hold_releases_on_landing_without_pending_flag_confirmation(self):
        ns, pc, calls, _, native, _ = self.setup_bridge((7, True, True, True, True))
        pc.Pawn.Physics = 2
        pc.bWantsToSprint = True
        ns.update(_assist_airborne_hold=False, _cfg_omni=True, _set_hook=lambda *args: None,
                  INPUT_ALLOWS_SPRINT='gate', Type=NS(PRE='pre'), Block='BLOCK',
                  _pc=lambda: pc, _debug=lambda message: None)
        load_definitions(MOD / '__init__.py', ['_hold_assist_through_airborne',
                          '_assist_grounded', '_assist_airborne_gate'], ns)
        self.assertTrue(ns['_hold_assist_through_airborne'](pc))
        self.assertFalse(pc.bWantsToSprint)
        self.assertTrue(ns['_assist_airborne_hold'])
        self.assertEqual(ns['_assist_airborne_gate'](None, None, None, None), ('BLOCK', False))
        pc.Pawn.Physics = 1
        native.intent_observe = lambda *args: (4, True, False, False, False)
        self.assertIsNone(ns['_assist_airborne_gate'](None, None, None, None))
        self.assertFalse(ns['_assist_airborne_hold'])

    def test_invalid_context_resets_python_ready_frames_before_stationary_path(self):
        ns, pc, calls, _, _, _ = self.setup_bridge(None)
        ns['_cfg_native_precision'] = False
        ns['_assist_recovery_ready_frames'] = 10
        ns['_basic_context'] = lambda *args, **kwargs: False
        pc.PlayerInput.aBaseY = 0
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertEqual(ns['_assist_recovery_ready_frames'], 0)
        self.assertNotIn(('try', True), calls)

    def landing_bridge(self, native_selected):
        ns, pc, calls, _, native, _ = self.setup_bridge(None)
        pc.bWantsToSprint = False
        pc.PlayerInput.aBaseY = pc.PlayerInput.aStrafe = 0.0
        ns.update(_cfg_native_precision=native_selected, _cfg_omni=True,
                  _assist_airborne_hold=False, _hooks={},
                  _pc=lambda: pc, _is_local_pawn=lambda pawn: pawn is pc.Pawn,
                  _set_hook=lambda tag, enabled, *args: ns['_hooks'].__setitem__(tag, enabled),
                  INPUT_ALLOWS_SPRINT='gate', PLAYER_TICK='tick',
                  Type=NS(PRE='pre', POST_UNCONDITIONAL='post'), Block='BLOCK',
                  _debug=lambda text: None)
        load_definitions(MOD / '__init__.py', ['_assist_raw_moving',
                         '_hold_assist_through_airborne', '_assist_grounded',
                         '_assist_airborne_gate', '_assist_recover_or_schedule',
                         '_schedule_assist_recovery', '_do_sprint_attempt',
                         '_assist_landed'], ns)
        # Native intent response models the same live input/physics facts. It
        # cannot grant CanSprint or mark actual engine sprint as confirmed.
        native.intent_observe = lambda *args: (
            (7, True, True, True, True) if pc.Pawn.Physics != 1
            else (3, True, True, True, False))
        return ns, pc, calls

    def test_accepted_airborne_request_cannot_be_lost_at_zero_input_landing(self):
        for native_selected in (False, True):
            with self.subTest(native=native_selected):
                ns, pc, calls = self.landing_bridge(native_selected)
                pc.Pawn.Physics = 2
                # Native DoSprint accepted permission, consumed bTryToSprint
                # and queued bWantsToSprint without ever entering sprint.
                pc.PlayerInput.bTryToSprint = False
                pc.bWantsToSprint = True
                ns['_do_sprint_attempt'](pc.Pawn, None, True, None)
                self.assertTrue(ns['_assist_airborne_hold'])
                self.assertFalse(pc.bWantsToSprint)
                self.assertIn(('try', True), calls)
                self.assertFalse(pc.bInSprintState)
                self.assertEqual(ns['_assist_airborne_gate'](None, None, None, None), ('BLOCK', False))
                # Landing cleanup clears both engine flags at zero input.
                pc.Pawn.Physics = 1
                pc.PlayerInput.bTryToSprint = pc.bWantsToSprint = False
                ns['_assist_airborne_gate'](None, None, None, None)
                calls.clear()
                ns['_assist_landed'](pc.Pawn, None, None, None)
                self.assertIn(('try', True), calls)
                self.assertTrue(ns['_armed'])
                self.assertFalse(pc.bInSprintState)

    def test_duck_release_just_before_landing_keeps_recovery_until_request_can_arm(self):
        for native_selected in (False, True):
            for moving in (False, True):
                with self.subTest(native=native_selected, moving=moving):
                    ns, pc, _ = self.landing_bridge(native_selected)
                    adapter = ns['_precision']
                    if native_selected:
                        native = adapter._native
                        ready_frames = 0

                        def intent(*args):
                            nonlocal ready_frames
                            armed, valid, sprinting, moves, grounded, allowed, pending, _ = args[4:]
                            if not valid:
                                phase = 1
                            elif not grounded:
                                phase = 7
                            elif not moves:
                                phase = 3
                            elif not allowed:
                                phase = 4
                            else:
                                ready_frames += 1
                                phase = 6 if ready_frames >= 2 else 5
                            request = phase in (3, 6, 7)
                            return phase, armed, request, request != pending, phase == 7

                        native.intent_observe = intent
                    now = [10.0]
                    active_hooks = set()

                    def set_hook(tag, enabled, *args):
                        if enabled:
                            active_hooks.add(tag)
                        else:
                            active_hooks.discard(tag)
                        return enabled

                    pi, pawn = pc.PlayerInput, pc.Pawn
                    pawn.Physics = 2
                    pawn.bIsCrouched = pawn.bWantsToCrouch = False
                    pc.bDuck = pc.bPressedSprint = False
                    pi.aBaseY = float(moving)
                    ns.update(
                        time=NS(perf_counter=lambda: now[0]), _try_sprint_written=None,
                        _hooks=active_hooks, _set_hook=set_hook, _crouch_sprint_pause_until=0.0,
                        _reset_gate_grace=lambda: None, _clear_direction=lambda: None,
                        _clear_center_transition=lambda: None,
                        _local_input=lambda obj: obj if obj is pi else None,
                    )
                    load_definitions(MOD / '__init__.py', [
                        '_assist_crouching', '_crouch_sprint_paused', '_set_try_sprint',
                        '_stop_assist_recovery', '_assist_crouch_changed', '_assist_duck_input',
                    ], ns)
                    # DuckPressed suspends the original airborne request.
                    pc.bDuck = True
                    ns['_assist_duck_input'](pi, None, None, None)
                    self.assertIn('assist-recovery', active_hooks)
                    self.assertFalse(pi.bTryToSprint)
                    # DuckReleased precedes the native physics change by one input edge.
                    now[0] = 10.12
                    pc.bDuck = False
                    ns['_assist_duck_input'](pi, None, None, None)
                    self.assertIn('assist-recovery', active_hooks)
                    self.assertFalse(ns['_assist_airborne_hold'])
                    self.assertFalse(pi.bTryToSprint)
                    pawn.Physics = 1
                    ns['_assist_landed'](pawn, None, None, None)
                    ns['_assist_recovery_tick'](pc, None, None, None)
                    self.assertIn('assist-recovery', active_hooks)
                    self.assertFalse(pi.bTryToSprint)
                    now[0] = 10.35
                    ns['_assist_recovery_tick'](pc, None, None, None)
                    if moving:
                        self.assertFalse(pi.bTryToSprint)
                        ns['_assist_recovery_tick'](pc, None, None, None)
                        self.assertIn('assist-recovery', active_hooks)
                    else:
                        self.assertNotIn('assist-recovery', active_hooks)
                    self.assertTrue(pi.bTryToSprint)
                    self.assertTrue(ns['_armed'])


    def test_landing_recovery_preserves_walk_locality_context_and_actual_sprint(self):
        for state in ('walk', 'remote', 'menu', 'already-sprinting'):
            with self.subTest(state=state):
                ns, pc, calls = self.landing_bridge(False)
                pawn = pc.Pawn
                if state == 'walk': ns['_armed'] = False
                elif state == 'remote': pawn = NS()
                elif state == 'menu': ns['_basic_context'] = lambda *args, **kw: False
                else: pc.bInSprintState = True
                ns['_assist_landed'](pawn, None, None, None)
                self.assertNotIn(('try', True), calls)
                self.assertNotIn('CanSprint', calls)

    def test_grounded_failed_request_keeps_permission_guarded_watcher(self):
        ns, pc, calls = self.landing_bridge(False)
        pc.PlayerInput.aBaseY = 1.0
        pc.Pawn.CanSprint = lambda: False
        ns['_do_sprint_attempt'](pc.Pawn, None, True, None)
        self.assertTrue(ns['_hooks']['assist-recovery'])
        calls.clear()
        ns['_assist_recovery_tick'](pc, None, None, None)
        self.assertIn(('try', False), calls)
        self.assertNotIn(('try', True), calls)
        self.assertFalse(pc.bInSprintState)

    def test_physical_press_toggle_walk_cleanup_and_backend_switch(self):
        ns, pc, calls, adapter, native, observations = self.setup_bridge(None)
        pi = pc.PlayerInput
        pc.bPressedSprint = pc.bWantsToSprint = True
        pc.EndSprint = lambda: calls.append('EndSprint')
        ns.update(_cfg_omni=True, _assist_press_held=False,
                  _travel_pending=True, _travel_initial_rearm_at=1.0,
                  _stop_travel_recovery=lambda reason: calls.append(('travel-stop', reason)),
                  _assist_airborne_hold=True, _gate_sprinting=True,
                  _local_input=lambda obj: pi, _pc=lambda: pc, Block='BLOCK',
                  INPUT_ALLOWS_SPRINT='gate', Type=NS(PRE='pre'),
                  _debug=lambda message: None, _set_hook=lambda *args: None,
                  _assist_airborne_gate=lambda *args: None,
                  _stop_crosshair_resync=lambda: None, _clear_direction=lambda: None,
                  _sync_sprint_hooks=lambda pc: None,
                  _assist_recover_or_schedule=lambda pc: calls.append('schedule'),
                  unrealsdk=NS(hooks=NS(prevent_hooking_direct_calls=nullcontext)))
        load_definitions(MOD / '__init__.py', ['_pressed', '_released'], ns)
        self.assertEqual(ns['_pressed'](pi, None, None, None), 'BLOCK')
        self.assertFalse(ns['_armed'])
        self.assertFalse(pc.bWantsToSprint or pc.bPressedSprint)
        self.assertIn('EndSprint', calls)
        self.assertIn(('travel-stop', 'walk-selected'), calls)
        # A repeated callback or backend reset while held cannot flip Walk.
        adapter.intent_reset(False)
        ns['_cfg_native_precision'] = False
        ns['_pressed'](pi, None, None, None)
        self.assertFalse(ns['_armed'])
        ns['_released'](pi, None, None, None)
        ns['_pressed'](pi, None, None, None)
        self.assertTrue(ns['_armed'])


if __name__ == '__main__':
    unittest.main()
