import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'mod' / 'OmniMovement' / '__init__.py'

def load(name, ns):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns[name]

class PauseReticleTests(unittest.TestCase):
    def test_pause_close_arms_repair_for_remembered_sprint(self):
        calls, offhand = [], []
        pc = NS(bInSprintState=False)
        ns = dict(_local_pc=lambda obj: pc, _active=True, _travel_pending=False,
                  _cfg_assist=True, _armed=True, _cfg_omni=True, _cfg_gap=0.1,
                  _cfg_crosshair=True, _crosshair_modal_open=True,
                  _stop_crosshair_resync=lambda: None,
                  _release_crosshair_visibility=lambda: None,
                  _clear_crosshair_widget=lambda: None,
                  _schedule_offhand_crosshair=offhand.append,
                  _schedule_crosshair_resync=lambda *a, **kw: calls.append(kw))
        fn = load('_crosshair_pause_state_post', ns)
        fn(pc, NS(bIsOpen=False), None, None)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]['defer_first'])
        self.assertTrue(calls[0]['force_tick'])
        self.assertEqual(calls[0]['max_attempts'], 720)
        self.assertEqual(offhand, [pc])
        ns['_armed'] = False
        fn(pc, NS(bIsOpen=False), None, None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(offhand, [pc])

    def test_lateral_melee_lurch_guard_preserves_native_exclusions(self):
        ns = dict(_knockback_until=0.0, time=NS(perf_counter=lambda: 1.0))
        eligible = load('_moving_nonforward_on_foot', ns)
        pawn = NS(Physics=1, bIsInjured=False, bIsDead=False,
                  bForceRMVelocity=False, bForceRegularVelocity=False, bRMForceStop=False)
        pi = NS(aBaseY=0.0, aStrafe=1.0)
        pc = NS(Pawn=pawn, PlayerInput=pi, bCinematicMode=False, bIgnoreMoveInput=False)
        self.assertTrue(eligible(pc))
        pi.aBaseY, pi.aStrafe = -1.0, 0.0
        self.assertTrue(eligible(pc))
        pi.aBaseY, pi.aStrafe = 1.0, 1.0
        self.assertFalse(eligible(pc))
        pi.aBaseY = 0.0
        pawn.bForceRMVelocity = True
        self.assertFalse(eligible(pc))
        pawn.bForceRMVelocity = False
        ns['_knockback_until'] = 2.0
        self.assertFalse(eligible(pc))

    def test_pause_grace_requires_existing_sprint_and_expires(self):
        ns = dict(_pause_sprint_grace_until=10.1, time=NS(perf_counter=lambda: 10.0))
        grace = load('_pause_sprint_grace', ns)
        pc = NS(bInSprintState=True)
        self.assertTrue(grace(pc))
        pc.bInSprintState = False
        self.assertFalse(grace(pc))
        pc.bInSprintState = True
        ns['time'] = NS(perf_counter=lambda: 10.11)
        self.assertFalse(grace(pc))

    def test_deferred_pause_repair_can_wait_for_sprint(self):
        hooks = []
        ns = dict(_active=True, _cfg_crosshair=True,
                  _crosshair_resync_ticks=0, _crosshair_resync_attempts=0,
                  _crosshair_resync_truthful_cache=False, _crosshair_resync_force_tick=False,
                  _crosshair_resync_native_widget_address=0, _crosshair_resync_last_request=None,
                  _clear_crosshair_widget=lambda: None,
                  _set_hook=lambda *a: hooks.append(a),
                  _set_optional_hook=lambda *a: False,
                  _hooks={}, CROSSHAIR_UPDATE='update', PLAYER_TICK='tick',
                  Type=NS(POST_UNCONDITIONAL='post'),
                  _crosshair_resync_tick=lambda *a: None,
                  _crosshair_resync_update=lambda *a: None)
        fn = load('_schedule_crosshair_resync', ns)
        pc = NS(bInSprintState=False)
        fn(pc, ticks=8, defer_first=True, truthful_cache=True, force_tick=True, max_attempts=720)
        self.assertEqual(ns['_crosshair_resync_ticks'], 8)
        self.assertEqual(ns['_crosshair_resync_attempts'], 720)
        self.assertTrue(any(a[0] == 'crosshair-resync-tick' and a[1] for a in hooks))
        ns['_crosshair_resync_ticks'] = 0
        fn(pc)
        self.assertEqual(ns['_crosshair_resync_ticks'], 0)

if __name__ == '__main__':
    unittest.main()
