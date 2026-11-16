"""Coyote timing, native jump boundary, vetoes and cleanup; no game simulation claims."""
import ast
import math
from types import SimpleNamespace as NS
import unittest

from test_combat_bridge import MOD, load_definitions


class Unset:
    pass


class CoyoteBridgeTests(unittest.TestCase):
    def harness(self):
        calls, hooks, clock = [], {}, [10.0]
        pawn = NS(_get_address=lambda: 30, Physics=1, Role=3,
                  bIsInjured=False, bIsDead=False, bDeleteMe=False,
                  bIsLeaping=False, DrivenVehicle=None, bJumpCapable=True,
                  bCanJump=True, bIsCrouched=False, bWantsToCrouch=False,
                  bIgnoreForces=False, WalkableFloorZ=0.7,
                  Velocity=NS(X=100.0, Y=-20.0, Z=0.0), CannotJumpNow=lambda: False)
        pi = NS(_get_address=lambda: 20, CurrentInputContextName='DefaultOnFoot')
        world = NS(_get_address=lambda: 40, Pauser=None, Game=NS(bLevelChange=False),
                   IsPreparingMapChange=lambda: False)
        pc = NS(_get_address=lambda: 10, Pawn=pawn, PlayerInput=pi, WorldInfo=world,
                bCinematicMode=False, bIgnoreMoveInput=False, bUpdating=False,
                bStopgapBlockForDeferredMovies=False, bPressedJump=True,
                PlayerReplicationInfo=NS(bGFxMenuOpen=False),
                IsLocalPlayerController=lambda: True, GetStateName=lambda: 'None',
                IsInTransitionalVehicleState=lambda: (False, 0.0))
        pawn.Controller = pc
        def set_hook(tag, enabled, path, kind, callback):
            calls.append(('hook', tag, enabled, path, kind))
            if enabled:
                hooks[tag] = callback
                return True
            hooks.pop(tag, None)
            return False
        ns = dict(_active=True, _cfg_coyote=True, _cfg_coyote_window=0.1,
                  _coyote_ready=True, _coyote_attempt=None, _coyote_failure_logged=False,
                  _coyote_traveling=False, _travel_pending=False,
                  _crosshair_modal_open=False, _deathtrap_skill_active=False,
                  _pc=lambda: pc, _local_input=lambda obj: pi if obj is pi else None,
                  _is_local_pawn=lambda obj: obj is not None and obj._get_address() == 30,
                  _local_pc=lambda obj: pc if obj is pc else None,
                  _cache_local=lambda obj: None, _set_hook=set_hook,
                  _function_exists=lambda path: True,
                  _debug=lambda message: calls.append(('debug', message)),
                  logging=NS(warning=lambda message: calls.append(('warning', message))),
                  on_foot=lambda p, i: i.CurrentInputContextName == 'DefaultOnFoot',
                  math=math, time=NS(monotonic=lambda: clock[0]), Unset=Unset, Block='block',
                  Type=NS(PRE=1, POST=2, POST_UNCONDITIONAL=3),
                  _armed=False, _gate_sprinting=True, _cfg_native_precision=False)
        source = ast.parse((MOD / '__init__.py').read_text())
        constants = ['JUMP_INPUT', 'DO_JUMP', 'BASE_CAN_JUMP', 'SET_PHYSICS',
                     'CROSSHAIR_FALLING', 'CROSSHAIR_LANDED', 'ADD_VELOCITY',
                     'CROSSHAIR_MODAL_OPENING', 'CROSSHAIR_PAUSE_STATE',
                     'SET_INPUT_CONTEXT', 'PRE_CLIENT_TRAVEL', 'NOTIFY_LOADED_WORLD',
                     'FAST_TRAVEL_REQUEST', 'FAST_TRAVEL_LOADED']
        constants.append('ACTION_SKILL_ENDED')
        for node in source.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in constants:
                    ns[node.targets[0].id] = ast.literal_eval(node.value)
        load_definitions(MOD / 'coyote.py', ['CoyoteTime'], ns)
        ns['_coyote'] = ns['CoyoteTime']()
        names = [node.name for node in source.body if isinstance(node, ast.FunctionDef)
                 and (node.name.startswith('_coyote_') or node.name == '_sync_coyote_hooks')]
        load_definitions(MOD / '__init__.py', names + ['_same', '_basic_context'], ns)
        def native_jump(updating):
            calls.append(('jump', updating))
            ns['_coyote_do_jump_pre'](pawn, NS(bUpdating=updating), Unset, None)
            override = hooks['coyote-can-jump'](pawn, None, Unset, None)
            calls.append(('base-override', override))
            result = override == ('block', True)
            ns['_coyote_do_jump_post'](pawn, None, result, None)
            return result
        pawn.DoJump = native_jump
        ns['_coyote_seed'](pc)
        return ns, pc, pi, pawn, clock, calls, hooks

    def leave(self, ns, pawn, clock):
        pawn.Physics = 2
        ns['_coyote_falling'](pawn, None, None, None)
        clock[0] += 0.04

    def land(self, ns, pawn):
        pawn.Physics = 1
        pawn.Velocity.Z = 0.0
        ns['_coyote_landed'](pawn, NS(FloorActor=object(), HitNormal=NS(Z=1.0)), None, None)

    def jump_count(self, calls):
        return sum(row[0] == 'jump' for row in calls)

    def test_one_immediate_native_attempt_both_backends_and_sprint_modes(self):
        for native in (False, True):
            for armed in (False, True):
                ns, pc, pi, pawn, clock, calls, hooks = self.harness()
                ns.update(_cfg_native_precision=native, _armed=armed)
                self.leave(ns, pawn, clock)
                original = (pawn.Physics, vars(pawn.Velocity).copy(), pc.bPressedJump)
                ns['_coyote_jump_input'](pi, None, None, None)
                self.assertIn(('base-override', ('block', True)), calls)
                self.assertEqual(self.jump_count(calls), 1)
                self.assertEqual(original, (pawn.Physics, vars(pawn.Velocity), pc.bPressedJump))
                self.assertEqual(ns['_armed'], armed)
                self.assertTrue(ns['_gate_sprinting'])
                self.assertIsNone(ns['_coyote_attempt'])
                self.assertNotIn('coyote-can-jump', hooks)
                for _ in range(3):
                    ns['_coyote_jump_input'](pi, None, None, None)
                self.assertEqual(self.jump_count(calls), 1)
                self.land(ns, pawn)
                self.assertEqual(self.jump_count(calls), 1, 'Landing must not execute queued input')
                self.leave(ns, pawn, clock)
                ns['_coyote_jump_input'](pi, None, None, None)
                self.assertEqual(self.jump_count(calls), 2)

    def test_deadline_is_elapsed_time_at_all_requested_frame_rates(self):
        for window in (0.1, 0.2, 1.0):
            for fps in (30, 60, 120, 144, 200, 240):
                ns, pc, _, pawn, clock, _, _ = self.harness()
                state, owner = ns['_coyote'], ns['_coyote_owner'](pc)
                pawn.Physics = 2
                ns['_coyote_falling'](pawn, None, None, None)
                for frame in range(math.ceil(window * fps)):
                    self.assertTrue(state.eligible(owner, 10.0 + frame / fps, window))
                self.assertFalse(state.eligible(owner, 10.0 + window, window))
                self.assertIsNone(state.left_at)

    def test_expired_input_is_ignored_and_never_replayed_on_landing(self):
        ns, _, pi, pawn, clock, calls, _ = self.harness()
        self.leave(ns, pawn, clock)
        clock[0] = 10.101
        ns['_coyote_jump_input'](pi, None, None, None)
        self.land(ns, pawn)
        self.assertEqual(self.jump_count(calls), 0)

    def test_normal_jump_cannot_arm_grace_even_if_falling_arrives_inside_do_jump(self):
        ns, pc, pi, pawn, clock, calls, _ = self.harness()
        ns['_coyote_do_jump_pre'](pawn, None, Unset, None)
        pawn.Physics = 2
        ns['_coyote_falling'](pawn, None, None, None)
        ns['_coyote_do_jump_post'](pawn, None, True, None)
        ns['_coyote_jump_input'](pi, None, None, None)
        self.assertEqual(self.jump_count(calls), 0)
        self.assertFalse(ns['_coyote'].eligible(ns['_coyote_owner'](pc), clock[0], 0.1))

    def test_falling_duplicates_do_not_extend_window_and_unknown_fall_does_not_arm(self):
        ns, pc, _, pawn, clock, _, _ = self.harness()
        self.leave(ns, pawn, clock)
        ns['_coyote_falling'](pawn, None, None, None)
        self.assertEqual(ns['_coyote'].left_at, 10.0)
        ns['_coyote'].invalidate()
        ns['_coyote_falling'](pawn, None, None, None)
        self.assertIsNone(ns['_coyote'].left_at)

    def test_launch_impulse_and_scripted_falling_never_grant_a_jump(self):
        for reason in ('rising', 'impulse', 'scripted'):
            ns, _, pi, pawn, clock, calls, _ = self.harness()
            if reason == 'rising':
                pawn.Velocity.Z = 20.0
            elif reason == 'impulse':
                ns['_coyote_impulse'](pawn, NS(NewVelocity=NS(X=1.0, Y=0.0, Z=0.0)), None, None)
            else:
                ns['_coyote_forced_physics'](pawn, NS(newPhysics=2), None, None)
            self.leave(ns, pawn, clock)
            ns['_coyote_jump_input'](pi, None, None, None)
            self.assertEqual(self.jump_count(calls), 0)

    def test_context_vetoes_and_controller_pawn_world_replacements(self):
        changes = [
            lambda pc,p,i: setattr(p, 'bIsInjured', True),
            lambda pc,p,i: setattr(p, 'bIsDead', True),
            lambda pc,p,i: setattr(p, 'bIsCrouched', True),
            lambda pc,p,i: setattr(p, 'bWantsToCrouch', True),
            lambda pc,p,i: setattr(p, 'bJumpCapable', False),
            lambda pc,p,i: setattr(p, 'bCanJump', False),
            lambda pc,p,i: setattr(p, 'DrivenVehicle', object()),
            lambda pc,p,i: setattr(p, 'bIsLeaping', True),
            lambda pc,p,i: setattr(p, 'Physics', 3),
            lambda pc,p,i: setattr(p, 'Role', 2),
            lambda pc,p,i: setattr(pc, 'bUpdating', True),
            lambda pc,p,i: setattr(pc, 'GetStateName', lambda: 'PlayerDriving'),
            lambda pc,p,i: setattr(pc, 'IsInTransitionalVehicleState', lambda: (True, 0.5)),
            lambda pc,p,i: setattr(pc, 'bCinematicMode', True),
            lambda pc,p,i: setattr(pc, 'bIgnoreMoveInput', True),
            lambda pc,p,i: setattr(pc.PlayerReplicationInfo, 'bGFxMenuOpen', True),
            lambda pc,p,i: setattr(pc, 'bStopgapBlockForDeferredMovies', True),
            lambda pc,p,i: setattr(i, 'CurrentInputContextName', 'Vehicle'),
            lambda pc,p,i: setattr(pc.WorldInfo, 'Pauser', object()),
            lambda pc,p,i: setattr(pc.WorldInfo.Game, 'bLevelChange', True),
            lambda pc,p,i: setattr(pc.WorldInfo, '_get_address', lambda: 41),
            lambda pc,p,i: setattr(p, '_get_address', lambda: 31),
            lambda pc,p,i: setattr(i, '_get_address', lambda: 21),
            lambda pc,p,i: setattr(pc, '_get_address', lambda: 11),
        ]
        for change in changes:
            ns, pc, pi, pawn, clock, calls, _ = self.harness()
            self.leave(ns, pawn, clock)
            change(pc, pawn, pi)
            ns['_coyote_jump_input'](pi, None, None, None)
            self.assertEqual(self.jump_count(calls), 0)

    def test_can_jump_override_is_only_in_own_native_call_and_respects_prior_veto(self):
        ns, pc, _, pawn, clock, _, _ = self.harness()
        pawn.Physics = 2
        fn = ns['_coyote_can_jump']
        self.assertIsNone(fn(pawn, None, Unset, None))
        ns['_coyote_attempt'] = (ns['_coyote_owner'](pc), clock[0] + 0.1)
        self.assertIsNone(fn(pawn, None, False, None))
        self.assertEqual(fn(pawn, None, Unset, None), ('block', True))
        self.assertIsNone(fn(NS(_get_address=lambda: 31), None, Unset, None))
        pc.bPressedJump = False
        self.assertIsNone(fn(pawn, None, Unset, None))
        pc.bPressedJump = True
        clock[0] += 0.1
        self.assertIsNone(fn(pawn, None, Unset, None))

    def test_native_failure_exception_and_reentrancy_clean_scope_without_retry(self):
        for outcome in ('false', 'exception', 'reentrant'):
            ns, _, pi, pawn, clock, calls, hooks = self.harness()
            self.leave(ns, pawn, clock)
            original = pawn.DoJump
            def native(updating):
                if outcome == 'exception':
                    calls.append(('jump', updating))
                    raise RuntimeError('blocked native boundary')
                if outcome == 'reentrant':
                    ns['_coyote_jump_input'](pi, None, None, None)
                result = original(updating)
                return False if outcome == 'false' else result
            pawn.DoJump = native
            ns['_coyote_jump_input'](pi, None, None, None)
            self.assertEqual(self.jump_count(calls), 1)
            self.assertIsNone(ns['_coyote_attempt'])
            self.assertNotIn('coyote-can-jump', hooks)
            ns['_coyote_jump_input'](pi, None, None, None)
            self.assertEqual(self.jump_count(calls), 1)

    def test_cannot_jump_now_and_invalidated_lifecycle_do_not_retry(self):
        for reason in ('cannot', 'death', 'modal', 'travel', 'disabled', 'zero-window'):
            ns, pc, pi, pawn, clock, calls, _ = self.harness()
            self.leave(ns, pawn, clock)
            if reason == 'cannot':
                pawn.CannotJumpNow = lambda: True
            elif reason == 'death':
                ns['_coyote_invalid_pawn'](pawn, None, None, None)
            elif reason == 'modal':
                ns['_coyote_invalid_controller'](pc, None, None, None)
            elif reason == 'travel':
                ns['_coyote_traveling'] = True
            elif reason == 'disabled':
                ns['_cfg_coyote'] = False
            else:
                ns['_cfg_coyote_window'] = 0.0
            ns['_coyote_jump_input'](pi, None, None, None)
            self.land(ns, pawn)
            self.assertEqual(self.jump_count(calls), 0)

    def test_hooks_are_event_only_and_remove_on_live_disable_or_zero_window(self):
        ns, _, _, _, _, calls, hooks = self.harness()
        ns['_sync_coyote_hooks']()
        self.assertTrue(ns['_coyote_ready'])
        self.assertNotIn('coyote-can-jump', hooks)
        self.assertFalse(any('Tick' in row[3] or 'ProcessMove' in row[3] or 'PreProcessInput' in row[3]
                             for row in calls if row[0] == 'hook'))
        ns['_cfg_coyote_window'] = 0.0
        ns['_sync_coyote_hooks']()
        self.assertFalse(ns['_coyote_ready'])
        self.assertEqual(hooks, {})
        ns['_cfg_coyote_window'] = 0.1
        ns['_function_exists'] = lambda path: path != ns['BASE_CAN_JUMP']
        ns['_sync_coyote_hooks']()
        self.assertFalse(ns['_coyote_ready'])
        self.assertEqual(hooks, {})

    def test_settings_have_stable_ids_defaults_and_persisted_option_registration(self):
        source = ast.parse((MOD / '__init__.py').read_text())
        settings = {}
        for node in source.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ('coyote_time', 'coyote_window'):
                    settings[node.targets[0].id] = {kw.arg: ast.literal_eval(kw.value) for kw in node.value.keywords}
        self.assertEqual((settings['coyote_time']['identifier'], settings['coyote_time']['value']), ('coyote_time', True))
        window = settings['coyote_window']
        self.assertEqual((window['identifier'], window['value'], window['min_value'], window['max_value'], window['step']),
                         ('coyote_window', 200, 0, 1000, 10))
        build = next(n.value for n in source.body if isinstance(n, ast.Assign)
                     and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
                     and n.value.func.id == 'build_mod')
        registered = next(kw.value for kw in build.keywords if kw.arg == 'options')
        self.assertTrue({'coyote_time', 'coyote_window'} <= {node.id for node in registered.elts})


if __name__ == '__main__':
    unittest.main()
