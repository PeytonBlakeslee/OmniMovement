"""Source-backed Zer0 Execute early sprint permission handoff tests.

ExecuteActionSkill restores movement control before CleanUpExecute. The previous
OmniMovement guard waited until final cleanup, even where the engine already
permitted ordinary sprint. No dash, melee or crouch rule is overridden here.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SRC = (Path(__file__).resolve().parents[1] / "mod" / "OmniMovement" /
       "__init__.py").read_text(encoding="utf-8-sig")
TREE = ast.parse(SRC)


def fn(name):
    return next(node for node in TREE.body
                if isinstance(node, ast.FunctionDef) and node.name == name)


class Clock:
    current = 10.0

    @classmethod
    def perf_counter(cls):
        return cls.current


class ZeroDashSprintHandoffTests(unittest.TestCase):
    def setUp(self):
        Clock.current = 10.0
        self.pawn = NS(
            Physics=1,
            _get_address=lambda: 202,
        )
        self.pi = NS(aBaseY=1.0, aStrafe=0.0)
        self.pc = NS(
            Pawn=self.pawn, PlayerInput=self.pi,
            _get_address=lambda: 101,
            bIgnoreMoveInput=False,
            bCinematicMode=False,
            bPerformingMeleeAttack=False,
            bPerformingSharedWeaponAction=False,
        )
        self.allowed = True
        self.moving = True
        self.context = True
        self.crouching = False
        self.pause = False
        self.momentum_calls = 0
        self.env = {
            "_zero_execute_waiting_cleanup": (303, 101, 202, 9.9),
            "_zero_execute_early_ready": False,
            "time": Clock,
            "_same": lambda a, b: a is b,
            "_assist_context": lambda *a, **kw: self.context,
            "_assist_crouching": lambda *a, **kw: self.crouching,
            "_crouch_sprint_paused": lambda: self.pause,
            "_assist_raw_moving": lambda *a, **kw: self.moving,
            "_assist_can_retry": lambda *a, **kw: self.allowed,
            "_zero_execute_restore_momentum": self.momentum,
            "_debug": lambda msg: None,
        }
        body = [fn("_zero_execute_waiting"), fn("_zero_execute_try_early_handoff")]
        exec(compile(ast.Module(body=body, type_ignores=[]),
                     "<zero-handoff>", "exec"), self.env)

    def momentum(self, pc, pi, pawn):
        self.momentum_calls += 1
        return True

    def attempt(self):
        return self.env["_zero_execute_try_early_handoff"](
            self.pc, self.pi, self.pawn)

    def test_releases_execute_hold_when_native_checks_pass(self):
        self.assertTrue(self.env["_zero_execute_waiting"]())
        self.assertTrue(self.attempt())
        self.assertFalse(self.env["_zero_execute_waiting"]())
        self.assertTrue(self.env["_zero_execute_early_ready"])
        self.assertEqual(self.momentum_calls, 1)
        self.assertFalse(self.attempt())
        self.assertEqual(self.momentum_calls, 1)

    def test_no_early_release_while_melee_or_control_lock(self):
        for attr in ("bPerformingMeleeAttack", "bPerformingSharedWeaponAction",
                     "bIgnoreMoveInput", "bCinematicMode"):
            with self.subTest(attr=attr):
                setattr(self.pc, attr, True)
                self.assertFalse(self.attempt())
                self.assertTrue(self.env["_zero_execute_waiting"]())
                setattr(self.pc, attr, False)
        self.assertEqual(self.momentum_calls, 0)

    def test_native_sprint_veto_and_input_blockers_are_authoritative(self):
        for attr in ("allowed", "moving", "context", "crouching", "pause"):
            with self.subTest(attr=attr):
                default = getattr(self, attr)
                setattr(self, attr, not default)
                self.assertFalse(self.attempt())
                self.assertTrue(self.env["_zero_execute_waiting"]())
                setattr(self, attr, default)

    def test_airborne_and_foreign_player_rejected(self):
        self.pawn.Physics = 2
        self.assertFalse(self.attempt())
        self.pawn.Physics = 1
        self.assertFalse(self.env["_zero_execute_try_early_handoff"](
            self.pc, self.pi, NS(Physics=1, _get_address=lambda: 999)))
        self.assertFalse(self.env["_zero_execute_try_early_handoff"](
            self.pc, NS(aBaseY=1.0), self.pawn))
        self.assertEqual(self.momentum_calls, 0)

    def test_expired_boundary_does_not_wake(self):
        Clock.current = 14.0
        self.assertFalse(self.attempt())
        self.assertFalse(self.env["_zero_execute_waiting"]())
        self.assertEqual(self.momentum_calls, 0)

    def test_existing_execute_cleanup_keeps_its_marker_and_resets_early_ready(self):
        restore_src = ast.get_source_segment(SRC, fn("_zero_execute_controls_restored"))
        clean_src = ast.get_source_segment(SRC, fn("_zero_execute_cleanup_completed"))
        tick_src = ast.get_source_segment(SRC, fn("_assist_recovery_tick"))
        self.assertIn("_zero_execute_waiting_cleanup is None", restore_src)
        self.assertIn("_zero_execute_early_ready = False", clean_src)
        self.assertIn("_zero_execute_try_early_handoff(pc, pi, pawn)", tick_src)
        self.assertIn("_zero_execute_restore_momentum", clean_src)
        self.assertIn("_zero_execute_seed_pending", clean_src)

    def test_crouch_fix_unchanged(self):
        crouch_src = ast.get_source_segment(SRC, fn("_assist_crouch_changed"))
        self.assertIn("_crouch_sprint_pause_until = time.perf_counter() + 0.22", crouch_src)
        self.assertIn("_force_end_sprint(pc)", crouch_src)
        self.assertIn("pc.bWantsToSprint = False", crouch_src)


if __name__ == "__main__":
    unittest.main()
