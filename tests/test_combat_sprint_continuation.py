import ast
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
        self.pawn = NS(Weapon=weapon, OffHandWeapon=None, Physics=1, bDeleteMe=False,
                       bIsCrouched=False, bWantsToCrouch=False, bReloading=False,
                       bForceRMVelocity=False, bForceRegularVelocity=False, bRMForceStop=False)
        self.pc = NS(Pawn=self.pawn, bInSprintState=True, bDuck=False, bPressedJump=False,
                     bPerformingSharedWeaponAction=True, bPerformingMeleeAttack=False,
                     PlayerClass=NS(SprintSettings=object()))
        self.events = []
        self.ns = dict(_active=True, _cfg_omni=True, _cfg_assist=True, _armed=True,
                       _cfg_hip_fire=True, _active_fire_entry=True,
                       _combat_action_active=True, _combat_fire_mask=0,
                       _is_local_pawn=lambda x: x is self.pawn, _pc=lambda: self.pc,
                       _same=lambda a,b: a is b, _basic_context=lambda *a,**k: True,
                       _fast_zoomed=lambda *a: False, Block='block',
                       _set_hook=lambda *a: self.events.append(a),
                       CONTINUE='continue', CAN_SPRINT='entry', Type=NS(PRE='pre'),
                       _assist_recover_or_schedule=lambda pc: self.events.append('recover'),
                       _local_pc=lambda obj: self.pc if obj is self.pc else None)
        load({'_combat_sprint_safe', '_combat_continue_override', '_combat_entry_override',
              '_sync_combat_sprint_hooks', '_combat_fire_edge',
              '_combat_action_begin', '_weapon_action_completed'}, self.ns)

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
