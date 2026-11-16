"""Source-sequenced Execute control restore THEN final native cleanup regression.

ExecuteActionSkill.CleanUpExecute calls WeaponActionComplete before its
second RestorePhysicsPostExecute, so early velocity and sprint rearming
must not run until that later engine callback has returned.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / "mod" / "OmniMovement" /
          "__init__.py").read_text(encoding="utf-8-sig")
TREE = ast.parse(SOURCE)


def body(name):
    return next(n for n in TREE.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


class ExecuteEndTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.clock = [10.0]
        self.pawn = NS(_get_address=lambda: 42)
        self.pc = NS(
            _get_address=lambda: 41,
            Pawn=self.pawn, PlayerInput=NS(), bInSprintState=False)
        self.skill = NS(_get_address=lambda: 43, MyWillowPC=self.pc,
                        MyWillowPawn=self.pawn,
                        bMovementHasBeenRestored=True,
                        bAttemptedExecute=True)
        self.momentum_allowed = True
        self.scope = {
            "_active": True, "_cfg_assist": True, "_armed": True,
            "_pc": lambda: self.pc,
            "_same": lambda a, b: a is b,
            "time": NS(perf_counter=lambda: self.clock[0]),
            "_zero_execute_waiting_cleanup": None,
            "_zero_execute_seed_pending": None,
            "_zero_execute_restore_momentum": (
                lambda pc, pi, pawn: self.calls.append("momentum")
                or self.momentum_allowed),
            "_set_try_sprint": lambda pi, v: self.calls.append(("try", v)),
            "_schedule_assist_recovery": lambda pc: self.calls.append("watch"),
            "_assist_recover_or_schedule": lambda pc: self.calls.append("rearm"),
            "_debug": lambda msg: None,
        }
        names = ("_zero_execute_waiting", "_zero_execute_controls_restored",
                 "_zero_execute_cleanup_completed")
        code = ast.Module(body=[body(n) for n in names], type_ignores=[])
        exec(compile(code, "<execute>", "exec"), self.scope)

    def restore(self):
        self.scope["_zero_execute_controls_restored"](self.skill, None, None, None)

    def cleanup(self):
        self.scope["_zero_execute_cleanup_completed"](self.skill, None, None, None)

    def test_second_native_zero_reset_precedes_our_reseed(self):
        self.restore()
        self.assertEqual(self.calls, [("try", False), "watch"])
        self.assertIsNotNone(self.scope["_zero_execute_waiting_cleanup"])
        # The game may move briefly, then final cleanup resets velocity.
        # The new code must not inject momentum until AFTER that reset.
        self.clock[0] += 0.4
        self.skill.bAttemptedExecute = False
        self.cleanup()
        self.assertEqual(self.calls, [("try", False), "watch", "momentum", "rearm"])
        self.assertIsNone(self.scope["_zero_execute_waiting_cleanup"])

    def test_repeated_native_control_return_does_not_reset_marker(self):
        self.restore()
        stamp = self.scope["_zero_execute_waiting_cleanup"]
        self.clock[0] += 0.2
        self.restore()
        self.assertEqual(stamp, self.scope["_zero_execute_waiting_cleanup"])

    def test_late_native_melee_permission_uses_existing_bounded_watcher(self):
        self.restore()
        self.momentum_allowed = False
        self.cleanup()
        self.assertIsNotNone(self.scope["_zero_execute_seed_pending"])
        self.assertEqual(self.calls[-2:], ["momentum", "rearm"])
        self.assertAlmostEqual(
            self.scope["_zero_execute_seed_pending"][2] - self.clock[0], 0.24)

    def test_other_pawn_skill_and_stale_completion_rejected(self):
        self.skill.MyWillowPawn = object()
        self.restore()
        self.assertIsNone(self.scope["_zero_execute_waiting_cleanup"])
        self.skill.MyWillowPawn = self.pawn
        self.restore()
        self.clock[0] += 3.1
        self.cleanup()
        self.assertNotIn("momentum", self.calls)

    def test_ignored_when_walk_or_no_execute(self):
        self.skill.bAttemptedExecute = False
        self.restore()
        self.assertEqual(self.calls, [])
        self.skill.bAttemptedExecute = True
        self.scope["_armed"] = False
        self.restore()
        self.assertEqual(self.calls, [])

    def test_no_early_unsafe_writes_or_dash_physics_changes(self):
        sync = ast.get_source_segment(SOURCE, body("_sync_feature_hooks"))
        early = ast.get_source_segment(SOURCE, body("_zero_execute_controls_restored"))
        final = ast.get_source_segment(SOURCE, body("_zero_execute_cleanup_completed"))
        self.assertIn("ZERO_EXECUTE_CLEANUP", sync)
        self.assertIn("Type.POST_UNCONDITIONAL", sync)
        self.assertNotIn("_zero_execute_restore_momentum(", early)
        self.assertIn("_zero_execute_restore_momentum(", final)
        self.assertNotIn("RestorePhysicsPostExecute(", early + final)
        self.assertNotIn("DoSprint(", early + final)


if __name__ == "__main__":
    unittest.main()
