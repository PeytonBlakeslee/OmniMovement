import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

PATH = Path(__file__).resolve().parents[1] / 'mod' / 'OmniMovement' / '__init__.py'

def load(names, ns):
    tree = ast.parse(PATH.read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(PATH), 'exec'), ns)

class CombatSprintTests(unittest.TestCase):
    def setUp(self):
        weapon = NS(IsFiring=lambda: False)
        self.pawn = NS(_get_address=lambda: 2, Weapon=weapon, OffHandWeapon=None, Physics=1, bDeleteMe=False,
                       bIsCrouched=False, bWantsToCrouch=False, bReloading=False,
                       bForceRMVelocity=False, bForceRegularVelocity=False, bRMForceStop=False,
                       CanContinueSprinting=lambda: True)
        self.pc = NS(_get_address=lambda: 1, Pawn=self.pawn, bInSprintState=True, bDuck=False, bPressedJump=False,
                     bPerformingSharedWeaponAction=True, bPerformingMeleeAttack=False,
                     PlayerClass=NS(SprintSettings=object()), WorldInfo=NS(Pauser=None),
                     PlayerReplicationInfo=NS(bGFxMenuOpen=False))
        self.events = []
        self.now = 10.0
        self.direct_hooks_suppressed = False
        @contextmanager
        def suppress_direct_hooks():
            self.direct_hooks_suppressed = True
            try:
                yield
            finally:
                self.direct_hooks_suppressed = False

        self.ns = dict(_active=True, _cfg_omni=True, _cfg_assist=True, _armed=True,
                       _cfg_hip_fire=True, _active_fire_entry=True,
                       _combat_action_active=True, _combat_fire_mask=0, _combat_fire_tail=None, time=NS(perf_counter=lambda: self.now),
                       _is_local_pawn=lambda x: x is self.pawn, _pc=lambda: self.pc,
                       _same=lambda a,b: a is b, _basic_context=lambda *a,**k: True,
                       _fast_zoomed=lambda *a: False, Block='block',
                       _crosshair_modal_open=False, _travel_modal_open=False, _knockback_until=0.0,
                       _assist_raw_moving=lambda pc: True, _interrupted=lambda pc: False,
                       _crouch_sprint_paused=lambda: False, _zero_execute_waiting=lambda: False,
                       _pose_reload_active=lambda pawn: False,
                       unrealsdk=NS(hooks=NS(prevent_hooking_direct_calls=suppress_direct_hooks)),
                       _set_hook=lambda *a: (self.events.append(a) or True),
                       CONTINUE='continue', CAN_SPRINT='entry', PLAYER_TICK='tick',
                       Type=NS(PRE='pre', POST_UNCONDITIONAL='post'),
                       _assist_recover_or_schedule=lambda pc: self.events.append('recover'),
                       _local_pc=lambda obj: self.pc if obj is self.pc else None)
        load({'_combat_sprint_safe', '_airborne_offhand_continuation', '_main_reload_allows_offhand_sprint', '_combat_release_safe', '_combat_continue_override', '_combat_entry_override',
              '_sync_combat_sprint_hooks', '_combat_fire_edge', '_combat_tail_matches',
              '_stop_combat_fire_tail', '_combat_fire_tail_tick', '_retain_combat_fire_tail',
              '_combat_action_begin', '_weapon_action_completed', '_weapon_action_pose_completed'}, self.ns)

    def test_action_completion_retries_pose_only_for_local_sprinting_player(self):
        self.ns['_sprint_fire_entry'] = lambda pc, **kw: self.events.append(('pose', kw))
        callback = self.ns['_weapon_action_pose_completed']
        callback(self.pc, None, None, None)
        self.pc.bInSprintState = False
        callback(self.pc, None, None, None)
        self.pc.bInSprintState = True
        self.ns['_cfg_hip_fire'] = False
        callback(self.pc, None, None, None)
        callback(object(), None, None, None)
        self.assertEqual(self.events, [('pose', {'after_equip': True})])

    def test_airborne_offhand_fire_only_preserves_an_existing_sprint(self):
        self.pc.PlayerInput = NS(CurrentInputContextName="Gunzerking")
        self.pc.bPerformingSharedWeaponAction = self.pc.bThrowingGrenade = False
        self.pc.bGunzerkingAutoSwitch = False
        self.pawn.OffHandWeapon = NS(IsFiring=lambda: True)
        self.pawn.Physics = 2
        self.ns["_combat_fire_mask"] = 2
        continuation = self.ns["_combat_continue_override"]
        self.assertEqual(continuation(self.pawn, None, None, None), ("block", True))
        self.pc.bInSprintState = False
        self.assertIsNone(self.ns["_combat_entry_override"](self.pawn, None, None, None))
        self.pc.bInSprintState = True
        self.pc.bThrowingGrenade = True
        self.assertIsNone(continuation(self.pawn, None, None, None))

    def test_main_reload_allows_offhand_fire_with_either_trigger_mapping(self):
        self.pc.PlayerInput = NS(CurrentInputContextName="Gunzerking")
        self.pc.bPerformingSharedWeaponAction = False
        self.pawn.bReloading = True
        self.pawn.Weapon = NS(_get_address=lambda: 3, GetStateName=lambda: "WeaponReloading",
                             IsFiring=lambda: False)
        self.pawn.OffHandWeapon = NS(_get_address=lambda: 4, GetStateName=lambda: "Active",
                                    IsFiring=lambda: True)
        self.ns['_pose_reload_addresses'] = set()
        for switched, mask in ((False, 2), (True, 1)):
            self.pc.bGunzerkingAutoSwitch = switched
            self.ns['_combat_fire_mask'] = mask
            self.pc.bInSprintState = True
            self.assertEqual(self.ns['_combat_continue_override'](self.pawn, None, None, None),
                             ('block', True))
            self.pc.bInSprintState = False
            self.assertEqual(self.ns['_combat_entry_override'](self.pawn, None, None, None),
                             ('block', True))
            self.ns['_pose_reload_addresses'].add(4)
            self.assertIsNone(self.ns['_combat_entry_override'](self.pawn, None, None, None))
            self.ns['_pose_reload_addresses'].clear()

    def test_action_continuation_keeps_root_motion_and_crouch_guards(self):
        f = self.ns['_combat_continue_override']
        self.assertEqual(f(self.pawn,None,None,None), ('block', True))
        self.pawn.bForceRMVelocity = True
        self.assertIsNone(f(self.pawn,None,None,None))
        self.pawn.bForceRMVelocity = False
        self.pc.bDuck = True
        self.assertIsNone(f(self.pawn,None,None,None))
        self.pc.bDuck = False
        self.ns['_combat_action_active'] = False
        self.assertIsNone(f(self.pawn,None,None,None))

    def test_action_never_overrides_zoom_unowned_or_unarmed_state(self):
        f = self.ns['_combat_continue_override']
        self.ns['_fast_zoomed'] = lambda *a: True
        self.assertIsNone(f(self.pawn,None,None,None))
        self.ns['_fast_zoomed'] = lambda *a: False
        self.ns['_armed'] = False
        self.assertIsNone(f(self.pawn,None,None,None))
        self.ns['_armed'] = True
        self.ns['_is_local_pawn'] = lambda obj: False
        self.assertIsNone(f(self.pawn,None,None,None))

    def test_offhand_fire_entry_and_continuation(self):
        self.pc.bPerformingSharedWeaponAction = False
        self.pawn.OffHandWeapon = NS(IsFiring=lambda: True)
        self.ns['_combat_action_active'] = False
        self.ns['_combat_fire_edge'](self.pc, 1, True)
        self.assertEqual(self.ns['_combat_continue_override'](self.pawn,None,None,None), ('block', True))
        self.pc.bInSprintState = False
        self.assertEqual(self.ns['_combat_entry_override'](self.pawn,None,None,None), ('block', True))
        self.pc.bPerformingMeleeAttack = True
        self.assertIsNone(self.ns['_combat_entry_override'](self.pawn,None,None,None))
        self.pc.bPerformingMeleeAttack = False
        self.ns['_combat_fire_edge'](self.pc, 1, False)
        self.assertIsNone(self.ns['_combat_entry_override'](self.pawn,None,None,None))
        self.assertEqual(self.ns['_combat_fire_mask'], 0)

    def test_offhand_sprint_entry_rejects_invalid_local_states(self):
        self.pc.bInSprintState = False
        self.pc.bPerformingSharedWeaponAction = False
        self.pawn.OffHandWeapon = NS(IsFiring=lambda: True)
        self.ns['_combat_fire_mask'] = 2
        entry = self.ns['_combat_entry_override']
        self.assertEqual(entry(self.pawn, None, None, None), ('block', True))

        blocked = (
            ('duck', self.pc, 'bDuck', True),
            ('jump', self.pc, 'bPressedJump', True),
            ('crouched', self.pawn, 'bIsCrouched', True),
            ('crouch-pending', self.pawn, 'bWantsToCrouch', True),
            ('root-motion', self.pawn, 'bForceRMVelocity', True),
            ('regular-velocity', self.pawn, 'bForceRegularVelocity', True),
            ('root-motion-stop', self.pawn, 'bRMForceStop', True),
            ('airborne', self.pawn, 'Physics', 2),
            ('retired-pawn', self.pawn, 'bDeleteMe', True),
            ('melee', self.pc, 'bPerformingMeleeAttack', True),
            ('shared-action', self.pc, 'bPerformingSharedWeaponAction', True),
            ('no-offhand', self.pawn, 'OffHandWeapon', None),
        )
        for name, owner, attribute, value in blocked:
            with self.subTest(case=name):
                original = getattr(owner, attribute)
                try:
                    setattr(owner, attribute, value)
                    self.assertIsNone(entry(self.pawn, None, None, None))
                finally:
                    setattr(owner, attribute, original)

        overrides = (
            ('foreign-pawn', '_is_local_pawn', lambda obj: False),
            ('invalid-context', '_basic_context', lambda *a, **kw: False),
            ('ads', '_fast_zoomed', lambda *a: True),
        )
        for name, key, replacement in overrides:
            with self.subTest(case=name):
                original = self.ns[key]
                try:
                    self.ns[key] = replacement
                    self.assertIsNone(entry(self.pawn, None, None, None))
                finally:
                    self.ns[key] = original

        self.assertEqual(entry(self.pawn, None, None, None), ('block', True))

    def test_release_keeps_override_until_offhand_firing_finishes(self):
        self.pc.bPerformingSharedWeaponAction = False
        self.ns['_combat_action_active'] = False
        firing = [True]
        self.pawn.OffHandWeapon = NS(_get_address=lambda: 3, IsFiring=lambda: firing[0])
        edge = self.ns['_combat_fire_edge']
        edge(self.pc, 1, True)
        edge(self.pc, 1, False)
        self.assertEqual(self.ns['_combat_fire_mask'], 0)
        self.assertEqual(self.ns['_combat_continue_override'](self.pawn,None,None,None), ('block', True))
        self.assertTrue(any(x[0] == 'combat-fire-tail' and x[1] for x in self.events if isinstance(x, tuple)))
        self.ns['_combat_fire_tail_tick'](self.pc,None,None,None)
        self.assertIsNotNone(self.ns['_combat_fire_tail'])
        firing[0] = False
        self.now += 0.11
        self.ns['_combat_fire_tail_tick'](self.pc,None,None,None)
        self.assertIsNone(self.ns['_combat_fire_tail'])
        self.assertIsNone(self.ns['_combat_continue_override'](self.pawn,None,None,None))

    def test_idle_offhand_release_preserves_only_existing_sprint(self):
        self.pc.bPerformingSharedWeaponAction = False
        self.ns['_combat_action_active'] = False
        self.pawn.OffHandWeapon = NS(_get_address=lambda: 3, IsFiring=lambda: False)
        edge = self.ns['_combat_fire_edge']
        edge(self.pc, 1, True)
        edge(self.pc, 1, False)
        self.ns['_combat_fire_tail_tick'](object(), None, None, None)
        self.now += 0.003
        self.ns['_combat_fire_tail_tick'](self.pc, None, None, None)
        continuation = self.ns['_combat_continue_override']
        self.assertEqual(continuation(self.pawn, None, None, None), ('block', True))
        self.pc.bInSprintState = False
        self.assertIsNone(self.ns['_combat_entry_override'](self.pawn, None, None, None))
        self.pc.bInSprintState = True
        self.now += 0.10
        self.ns['_combat_fire_tail_tick'](self.pc, None, None, None)
        self.assertIsNone(continuation(self.pawn, None, None, None))

    def test_release_waits_for_native_gate_after_reflected_guns_report_idle(self):
        self.pc.bPerformingSharedWeaponAction = False
        self.ns['_combat_action_active'] = False
        self.pawn.OffHandWeapon = NS(_get_address=lambda: 3, IsFiring=lambda: False)
        ready = False
        probes = []

        def native_continue():
            self.assertTrue(self.direct_hooks_suppressed)
            probes.append(ready)
            return ready

        self.pawn.CanContinueSprinting = native_continue
        edge = self.ns['_combat_fire_edge']
        edge(self.pc, 1, True)
        edge(self.pc, 1, False)
        tick = self.ns['_combat_fire_tail_tick']
        continue_sprint = self.ns['_combat_continue_override']
        self.now += 0.11
        tick(self.pc, None, None, None)
        self.assertEqual(continue_sprint(self.pawn, None, None, None), ('block', True))
        self.assertEqual(probes, [False])
        self.ns['_pose_reload_active'] = lambda pawn: True
        self.assertIsNone(continue_sprint(self.pawn, None, None, None))
        self.ns['_pose_reload_active'] = lambda pawn: False
        self.ns['_assist_raw_moving'] = lambda pc: False
        self.assertIsNone(continue_sprint(self.pawn, None, None, None))
        self.ns['_assist_raw_moving'] = lambda pc: True
        self.now += 0.35
        ready = True
        tick(self.pc, None, None, None)
        self.assertEqual(probes, [False, True])
        self.assertIsNone(self.ns['_combat_fire_tail'])
        self.assertIsNone(continue_sprint(self.pawn, None, None, None))

    def test_release_tail_never_overrides_crouch_or_replaced_weapon(self):
        self.pc.bPerformingSharedWeaponAction = False
        self.ns['_combat_action_active'] = False
        self.pawn.OffHandWeapon = NS(_get_address=lambda: 3, IsFiring=lambda: True)
        self.ns['_combat_fire_edge'](self.pc, 1, True)
        self.ns['_combat_fire_edge'](self.pc, 1, False)
        self.pc.bDuck = True
        self.assertIsNone(self.ns['_combat_continue_override'](self.pawn,None,None,None))
        self.pc.bDuck = False
        self.pawn.OffHandWeapon = NS(_get_address=lambda: 4, IsFiring=lambda: True)
        self.assertIsNone(self.ns['_combat_continue_override'](self.pawn,None,None,None))
        self.ns['_combat_fire_tail_tick'](self.pc,None,None,None)
        self.assertIsNone(self.ns['_combat_fire_tail'])

    def test_repeated_press_replaces_tail_without_dropping_fire_mask(self):
        self.pc.bPerformingSharedWeaponAction = False
        self.ns['_combat_action_active'] = False
        self.pawn.OffHandWeapon = NS(_get_address=lambda: 3, IsFiring=lambda: True)
        edge = self.ns['_combat_fire_edge']
        for _ in range(4):
            edge(self.pc, 1, True)
            self.assertIsNone(self.ns['_combat_fire_tail'])
            edge(self.pc, 1, False)
            self.assertIsNotNone(self.ns['_combat_fire_tail'])
            self.assertEqual(self.ns['_combat_continue_override'](self.pawn,None,None,None), ('block', True))

    def test_tail_expires_and_hook_registration_failure_fails_closed(self):
        self.pc.bPerformingSharedWeaponAction = False
        self.ns['_combat_action_active'] = False
        self.pawn.OffHandWeapon = NS(_get_address=lambda: 3, IsFiring=lambda: True)
        edge = self.ns['_combat_fire_edge']
        edge(self.pc, 1, True); edge(self.pc, 1, False)
        pc_id, pawn_id, weapon_id, grace, _ = self.ns['_combat_fire_tail']
        self.ns['_combat_fire_tail'] = (pc_id, pawn_id, weapon_id, grace, self.now - 1)
        self.assertIsNone(self.ns['_combat_continue_override'](self.pawn,None,None,None))
        self.ns['_combat_fire_tail_tick'](self.pc,None,None,None)
        self.assertIsNone(self.ns['_combat_fire_tail'])
        self.ns['_set_hook'] = lambda *a: False
        edge(self.pc, 1, True); edge(self.pc, 1, False)
        self.assertIsNone(self.ns['_combat_fire_tail'])

    def test_tail_supports_either_input_mapping_and_sprint_entry_setting(self):
        self.pc.bPerformingSharedWeaponAction = False
        self.ns['_combat_action_active'] = False
        self.pawn.OffHandWeapon = NS(_get_address=lambda: 3, IsFiring=lambda: True)
        edge = self.ns['_combat_fire_edge']
        edge(self.pc, 0, True); edge(self.pc, 0, False)
        self.pc.bInSprintState = False
        self.assertEqual(self.ns['_combat_entry_override'](self.pawn,None,None,None), ('block', True))
        self.ns['_active_fire_entry'] = False
        self.assertIsNone(self.ns['_combat_entry_override'](self.pawn,None,None,None))

    def test_action_hooks_are_scoped_to_local_begin_and_completion(self):
        self.ns['_combat_action_active'] = False
        self.ns['_combat_action_begin'](object(), None, None, None)
        self.assertFalse(self.ns['_combat_action_active'])
        self.ns['_combat_action_begin'](self.pc, None, None, None)
        self.assertTrue(self.ns['_combat_action_active'])
        self.assertTrue(any(x[0] == 'combat-continue' and x[1] for x in self.events if isinstance(x, tuple)))
        self.ns['_weapon_action_completed'](self.pc, None, None, None)
        self.assertFalse(self.ns['_combat_action_active'])
        self.assertTrue(any(x[0] == 'combat-continue' and not x[1] for x in self.events if isinstance(x, tuple)))

    def test_two_fire_inputs_keep_hook_until_both_released(self):
        self.pawn.OffHandWeapon = NS(IsFiring=lambda: True)
        edge = self.ns['_combat_fire_edge']
        edge(self.pc, 0, True); edge(self.pc, 1, True); edge(self.pc, 0, False)
        self.assertEqual(self.ns['_combat_fire_mask'], 2)
        edge(self.pc, 1, False)
        self.assertEqual(self.ns['_combat_fire_mask'], 0)

if __name__ == '__main__': unittest.main()
