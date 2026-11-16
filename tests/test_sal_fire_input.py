import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'mod' / 'OmniMovement' / '__init__.py'

def load(names, ns):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)

class SalFireInputTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.pawn = NS(OffHandWeapon=object(), bReloading=False)
        self.pc = NS(Pawn=self.pawn, bInSprintState=True, WorldInfo=NS(TimeSeconds=10.0))
        self.ns = dict(_active=True, _cfg_hip_fire=True, _cfg_assist=False, _armed=False,
            _active_fire_entry=True, _fire_requested=False, _pose_offhand_requested=False,
            _pose_release_at=0.0, _hip_hooks_until=0.0, _hip_hooks_live=False,
            _fire_until=0.0, _sprint_fire_entry_remaining=0, _pose_reload_retry=0,
            _pose_reload_addresses=set(), _local_pc=lambda obj: self.pc, _pc=lambda: self.pc,
            _combat_fire_edge=lambda *a: None, _stop_fire_state_recovery=lambda: None,
            _clear_direction=lambda: None, _sync_fire_hooks=lambda b: self.events.append(('hooks',b)),
            _update_fire_pose=lambda pc, **kw: self.events.append(('pose',kw['firing'])),
            _schedule_hip_cleanup=lambda: self.events.append(('cleanup',)),
            _stop_sprint_fire_entry_probe=lambda: self.events.append(('probe-stop',)),
            _set_hook=lambda *a: self.events.append(('set-hook', a[0], a[1])),
            _weapon_belongs_to_local=lambda obj: True,
            _fire_pose=NS(restore=lambda: self.events.append(('restore',)), aliases_active=True),
            PLAYER_TICK='tick', Type=NS(POST_UNCONDITIONAL='post'))
        load({'_start_fire','_stop_fire','_pose_fire_requested','_pose_reload_begin',
              '_pose_reload_end','_stop_pose_reload_retry','_pose_reload_retry_tick'}, self.ns)

    def start(self, mode): self.ns['_start_fire'](self.pc, NS(FireModeNum=mode), None, None)
    def stop(self, mode): self.ns['_stop_fire'](self.pc, NS(FireModeNum=mode), None, None)

    def test_left_only_requests_firing_pose_and_releases(self):
        self.start(1)
        self.assertTrue(self.ns['_pose_offhand_requested'])
        self.assertIn(('pose',True), self.events)
        self.stop(1)
        self.assertFalse(self.ns['_pose_fire_requested']())
        self.assertIn(('cleanup',), self.events)

    def test_either_release_order_preserves_other_hand(self):
        for first, second in ((0,1),(1,0)):
            with self.subTest(first=first):
                self.setUp(); self.start(0); self.start(1); self.events.clear()
                self.stop(first)
                self.assertTrue(self.ns['_pose_fire_requested']())
                self.assertNotIn(('cleanup',), self.events)
                self.stop(second)
                self.assertFalse(self.ns['_pose_fire_requested']())
                self.assertIn(('cleanup',), self.events)

    def test_secondary_input_without_offhand_does_not_change_pose(self):
        self.pawn.OffHandWeapon = None
        self.start(1)
        self.assertFalse(self.ns['_pose_fire_requested']())
        self.assertNotIn(('pose',True), self.events)

    def test_offhand_reload_is_detected_without_main_pawn_flag(self):
        load({'_pose_reload_active'}, self.ns)
        main = NS(_get_address=lambda: 1, GetStateName=lambda: 'Active')
        off = NS(_get_address=lambda: 2, GetStateName=lambda: 'WeaponReloading')
        self.pawn.Weapon, self.pawn.OffHandWeapon = main, off
        self.assertTrue(self.ns['_pose_reload_active'](self.pawn))
        off.GetStateName = lambda: 'Active'
        self.assertFalse(self.ns['_pose_reload_active'](self.pawn))
        self.ns['_pose_reload_addresses'].add(99)
        self.assertFalse(self.ns['_pose_reload_active'](self.pawn))

    def test_reload_releases_pose_until_both_weapons_finish(self):
        a=NS(_get_address=lambda: 101); b=NS(_get_address=lambda: 102)
        self.start(1); self.events.clear()
        self.ns['_pose_reload_begin'](a,None,None,None)
        self.ns['_pose_reload_begin'](b,None,None,None)
        self.assertEqual(self.ns['_pose_reload_addresses'], {101,102})
        self.assertIn(('restore',), self.events)
        self.events.clear(); self.ns['_pose_reload_end'](a,None,None,None)
        self.assertNotIn(('set-hook','pose-reload-retry',True), self.events)
        self.ns['_pose_reload_end'](b,None,None,None)
        self.assertIn(('set-hook','pose-reload-retry',True), self.events)
        self.ns['_pose_reload_retry_tick'](self.pc,None,None,None)
        self.assertIn(('pose',True), self.events)
        self.assertEqual(self.ns['_pose_reload_retry'],0)

if __name__ == '__main__': unittest.main()
